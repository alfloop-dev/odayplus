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
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")

DEFAULT_TTL_HOURS = 24
MAX_TTL_HOURS = 168  # 7 days max allowed extension
DEFAULT_EPHEMERAL_STATE_DIR = Path("/tmp/oday-plus-ephemeral-staging")
DEFAULT_EPHEMERAL_MODULE_DIR = Path("infra/terraform/modules/ephemeral_staging")


# --- Data Structures ---


@dataclasses.dataclass(frozen=True)
class StagingConfig:
    release_id: str
    candidate_sha: str
    manifest_digest: str
    project_id: str
    region: str = "asia-east1"
    cloud_sql_instance_name: str = "oday-staging-db"
    cloud_sql_connection_name: str = "project:asia-east1:oday-staging-db"
    network_name: str = "oday-staging-vpc"
    subnetwork_name: str = "oday-staging-subnet"
    kms_key_id: str = "projects/p/locations/asia-east1/keyRings/r/cryptoKeys/k"
    deployer_service_account_email: str = "deployer@project.iam.gserviceaccount.com"
    api_image: str = "asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "0" * 64
    web_image: str = "asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "0" * 64
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


def get_ephemeral_resource_names(release_id: str, project_id: str = "") -> dict[str, str]:
    """Compute collision-free, length-compliant GCP resource names for ephemeral staging."""
    clean = sanitize_release_suffix(release_id)
    rel_hash = compute_release_hash(release_id)

    sa_slug = clean[:13]
    sa_prefix = f"stg-{sa_slug}-{rel_hash}"

    db_slug_clean = clean.replace("-", "_")
    db_slug = db_slug_clean[:40]
    db_user_slug = db_slug_clean[:36]

    bucket_slug = clean[:12]
    bucket_name = f"stg-{bucket_slug}-{rel_hash}-data-{project_id}" if project_id else f"stg-{bucket_slug}-{rel_hash}-data"

    res_slug = clean[:24]
    name_prefix = f"stg-{res_slug}-{rel_hash}"

    return {
        "name_prefix": name_prefix,
        "release_hash": rel_hash,
        "sa_runtime": f"{sa_prefix}-rt",
        "sa_web": f"{sa_prefix}-web",
        "sa_worker": f"{sa_prefix}-wkr",
        "database_name": f"stg_{db_slug}_{rel_hash}",
        "database_user": f"stg_{db_user_slug}_{rel_hash}_app",
        "bucket_name": bucket_name,
        "cloud_run_api": f"{name_prefix}-api",
        "cloud_run_web": f"{name_prefix}-web",
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

    labels: dict[str, str] = {
        "app": "oday-plus",
        "environment": "staging",
        "managed_by": "terraform",
        "ephemeral": "true",
        "release_id": release_suffix,
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
        errors.append("kms_key_id must be non-empty.")

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
    now_dt = created_at or (parse_timestamp(config.created_at) if config.created_at else (now or datetime.now(UTC)))
    errors = validate_staging_config(config, now=now_dt)
    if errors:
        raise ValueError(f"Cannot generate tfvars for invalid config: {'; '.join(errors)}")

    return {
        "project_id": config.project_id,
        "region": config.region,
        "release_id": config.release_id,
        "candidate_sha": config.candidate_sha,
        "manifest_digest": config.manifest_digest,
        "api_image": config.api_image,
        "web_image": config.web_image,
        "ttl_hours": config.ttl_hours,
        "created_at": format_timestamp(now_dt),
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
    now_dt = created_at or (parse_timestamp(config.created_at) if config.created_at else (now or datetime.now(UTC)))
    expires = now_dt + timedelta(hours=config.ttl_hours)
    labels = generate_staging_labels(
        release_id=config.release_id,
        candidate_sha=config.candidate_sha,
        manifest_digest=config.manifest_digest,
        owner_task_id=config.owner_task_id,
        ttl_hours=config.ttl_hours,
        created_at=now_dt,
        additional_labels=config.additional_labels,
    )

    names = get_ephemeral_resource_names(config.release_id, config.project_id)
    created_iso = format_timestamp(now_dt)
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


def make_terraform_creation_executor(
    *,
    module_dir: Path = DEFAULT_EPHEMERAL_MODULE_DIR,
    state_dir: Path = DEFAULT_EPHEMERAL_STATE_DIR,
    terraform_bin: str = "terraform",
    initialize: bool = True,
) -> Callable[[StagingConfig, Sequence[StagingResource]], bool]:
    """Build the live create executor used by the CLI.

    Terraform state and tfvars are isolated by release id. The planned
    ``created_at`` is persisted into tfvars before apply, so a subsequent
    apply uses the same timestamp rather than refreshing the TTL.
    """
    module_path = module_dir.expanduser().resolve()
    state_path_root = state_dir.expanduser().resolve()

    def execute(config: StagingConfig, resources: Sequence[StagingResource]) -> bool:
        if not module_path.is_dir():
            raise RuntimeError(f"Terraform module directory does not exist: {module_path}")
        if not resources:
            raise RuntimeError("Terraform create received an empty resource plan")

        state_path_root.mkdir(parents=True, exist_ok=True)
        state_path, tfvars_path, inventory_path = _terraform_state_paths(config.release_id, state_path_root)

        # Preserve existing authoritative created_at if already provisioned
        created_at = config.created_at or resources[0].created_at
        if tfvars_path.is_file():
            prev_vars = json.loads(tfvars_path.read_text(encoding="utf-8"))
            previous_created_at = str(prev_vars.get("created_at", "")).strip()
            if previous_created_at:
                if config.created_at and parse_timestamp(config.created_at) != parse_timestamp(
                    previous_created_at
                ):
                    raise RuntimeError(
                        "Existing release state has an authoritative created_at; "
                        "rerun cannot replace it."
                    )
                created_at = previous_created_at

        apply_config = dataclasses.replace(config, created_at=created_at)
        authoritative_created_dt = parse_timestamp(created_at)
        authoritative_labels = generate_staging_labels(
            release_id=apply_config.release_id,
            candidate_sha=apply_config.candidate_sha,
            manifest_digest=apply_config.manifest_digest,
            owner_task_id=apply_config.owner_task_id,
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

        if initialize:
            _run_terraform(
                module_dir=module_path,
                terraform_bin=terraform_bin,
                arguments=["init", "-input=false", "-upgrade=false"],
            )
        _run_terraform(
            module_dir=module_path,
            terraform_bin=terraform_bin,
            arguments=[
                "apply",
                "-input=false",
                "-auto-approve",
                f"-state={state_path}",
                f"-var-file={tfvars_path}",
            ],
        )
        return True

    return execute


def make_terraform_deletion_executor(
    release_id: str,
    *,
    module_dir: Path = DEFAULT_EPHEMERAL_MODULE_DIR,
    state_dir: Path = DEFAULT_EPHEMERAL_STATE_DIR,
    terraform_bin: str = "terraform",
    initialize: bool = True,
) -> Callable[[Mapping[str, Any]], bool]:
    """Build a release-scoped live destroy executor.

    The first exact-label match performs one destroy against that release's
    state. Later matches are acknowledged because the same Terraform destroy
    removed the complete release graph. No project-wide destroy is possible.
    """
    module_path = module_dir.expanduser().resolve()
    state_path_root = state_dir.expanduser().resolve()
    state_path, tfvars_path, inventory_path = _terraform_state_paths(release_id, state_path_root)
    attempted = False
    destroy_success = False

    def execute(_resource: Mapping[str, Any]) -> bool:
        nonlocal attempted, destroy_success
        if attempted:
            return destroy_success
        attempted = True

        if not module_path.is_dir():
            raise RuntimeError(f"Terraform module directory does not exist: {module_path}")
        if not state_path.is_file() or not tfvars_path.is_file():
            raise RuntimeError(
                f"No release-scoped Terraform state for cleanup of {release_id!r}: {state_path}"
            )
        if initialize:
            _run_terraform(
                module_dir=module_path,
                terraform_bin=terraform_bin,
                arguments=["init", "-input=false", "-upgrade=false"],
            )
        _run_terraform(
            module_dir=module_path,
            terraform_bin=terraform_bin,
            arguments=[
                "destroy",
                "-input=false",
                "-auto-approve",
                f"-state={state_path}",
                f"-var-file={tfvars_path}",
            ],
        )
        destroy_success = True
        # The live resources are gone; remove the local release receipt inputs
        # so a later cleanup cannot mistake stale inventory for live resources.
        for path in (state_path, tfvars_path, inventory_path):
            path.unlink(missing_ok=True)
        return True

    return execute


def create_ephemeral_staging(
    config: StagingConfig,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    creation_executor: Callable[[StagingConfig, Sequence[StagingResource]], bool | Sequence[dict[str, Any]]] | None = None,
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
    try:
        exec_res = creation_executor(config, planned)
        if isinstance(exec_res, Sequence) and not isinstance(exec_res, (str, bytes, bytearray)):
            exec_success = bool(exec_res)
        else:
            exec_success = bool(exec_res)
    except Exception as exc:
        exec_success = False
        errors.append(f"Creation executor failed: {exc}")

    cleanup_receipt: dict[str, Any] | None = None
    if not exec_success:
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

    remediation_required = not exec_success
    remediation_notes = ""
    if not exec_success:
        if cleanup_receipt and cleanup_receipt.get("success"):
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
    if cleanup_receipt is not None:
        metadata["failure_cleanup_receipt"] = cleanup_receipt

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

    for res in inventory:
        labels = res.get("labels", {})
        if not isinstance(labels, Mapping):
            continue

        # Strictly check full label match
        if is_staging_ephemeral_resource(labels, release_id):
            matching_resources.append(res)

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
    cleaned_resources: list[dict[str, Any]] = []
    failed_cleanups = 0
    alerts: list[str] = []
    remediation_tasks: list[dict[str, Any]] = []

    for res in resource_inventory:
        labels = res.get("labels", {})
        if not isinstance(labels, Mapping):
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

    # Required labels check
    required_labels = (
        "release_id",
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

    # created_at variable check
    if 'variable "created_at"' not in vars_text:
        errors.append("variables.tf is missing required variable `created_at` for idempotent applies.")

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

    # create
    create_p = subparsers.add_parser("create", help="Plan or create ephemeral staging")
    create_p.add_argument("--release-id", required=True, help="Release identifier")
    create_p.add_argument("--candidate-sha", required=True, help="Exact 40-character commit SHA")
    create_p.add_argument("--manifest-digest", required=True, help="SHA256 manifest digest")
    create_p.add_argument("--project-id", required=True, help="GCP Project ID")
    create_p.add_argument("--region", default="asia-east1", help="GCP Region")
    create_p.add_argument("--cloud-sql-instance", default="oday-staging-db", help="Cloud SQL instance")
    create_p.add_argument("--api-image", required=True, help="API image reference with @sha256")
    create_p.add_argument("--web-image", required=True, help="Web image reference with @sha256")
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
    add_terraform_options(create_p)

    # cleanup
    clean_p = subparsers.add_parser("cleanup", help="Clean up ephemeral staging resources")
    clean_p.add_argument("--release-id", required=True, help="Target release identifier")
    clean_p.add_argument("--project-id", required=True, help="GCP Project ID")
    clean_p.add_argument("--dry-run", action="store_true", help="Perform dry-run without deletion")
    clean_p.add_argument("--inventory-file", help="JSON file with resource inventory for label filtering")
    clean_p.add_argument("--allow-empty", action="store_true", help="Allow empty inventory without error")
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
            ttl_hours=args.ttl_hours,
            created_at=created_at_to_use,
            owner_task_id=args.owner_task_id,
        )

        creation_executor = None
        cleanup_executor = None
        if not args.dry_run:
            creation_executor = make_terraform_creation_executor(
                module_dir=Path(args.terraform_module_dir),
                state_dir=Path(args.state_dir),
                terraform_bin=args.terraform_bin,
                initialize=not args.skip_terraform_init,
            )
            cleanup_executor = make_terraform_deletion_executor(
                args.release_id,
                module_dir=Path(args.terraform_module_dir),
                state_dir=Path(args.state_dir),
                terraform_bin=args.terraform_bin,
                initialize=not args.skip_terraform_init,
            )
        receipt = create_ephemeral_staging(
            config,
            dry_run=args.dry_run,
            creation_executor=creation_executor,
            cleanup_executor=cleanup_executor,
        )
        if args.tfvars_out and (receipt.success or args.dry_run):
            tfvars = generate_tfvars(config)
            Path(args.tfvars_out).write_text(json.dumps(tfvars, indent=2), encoding="utf-8")

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
            )

        receipt = cleanup_ephemeral_staging(
            release_id=args.release_id,
            project_id=args.project_id,
            resource_inventory=inventory,
            dry_run=args.dry_run,
            deletion_executor=deletion_executor,
            allow_empty=args.allow_empty,
        )
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
                executor = make_terraform_deletion_executor(
                    target_id,
                    module_dir=Path(args.terraform_module_dir),
                    state_dir=Path(args.state_dir),
                    terraform_bin=args.terraform_bin,
                    initialize=not args.skip_terraform_init,
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
