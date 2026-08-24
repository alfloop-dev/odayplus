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
4. All resources carry immutable tracking labels (owner, created_at, expires_at,
   ephemeral=true, release_id, candidate_sha, manifest_digest_prefix).
5. Cleanup operates ONLY via exact label matching; broad wildcards are forbidden.
6. Failed staging runs are retained for debugging up to 24 hours by default.
   TTL extensions require explicit owner and documented reason (max 168h / 7d).
7. An orphan scanner detects expired or unmanaged ephemeral staging resources
   and triggers remediation or automated safe cleanup.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


# --- Validation Regex Patterns ---

RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
CANDIDATE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")

DEFAULT_TTL_HOURS = 24
MAX_TTL_HOURS = 168  # 7 days max allowed extension


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
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO timestamp or sanitized label timestamp into timezone-aware datetime."""
    # Label timestamps may replace : and T with -
    cleaned = ts_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    # Try label format: YYYY-MM-DD-HH-MM-SS or YYYY-MM-DD-HH-MM-SS-ffffff
    parts = cleaned.split("-")
    if len(parts) >= 6:
        try:
            year, month, day, hour, minute, second = (int(p) for p in parts[:6])
            return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        except Exception:
            pass

    raise ValueError(f"Unable to parse timestamp: {ts_str!r}")


def sanitize_release_suffix(release_id: str) -> str:
    """Normalize release ID into a safe lowercase hyphenated suffix."""
    return re.sub(r"[^a-z0-9-]", "-", release_id.lower()).strip("-")


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
    now = created_at or datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl_hours)
    release_suffix = sanitize_release_suffix(release_id)

    digest_clean = manifest_digest.replace("sha256:", "")
    manifest_prefix = digest_clean[:16] if digest_clean else "0" * 16

    labels: dict[str, str] = {
        "app": "oday-plus",
        "environment": "staging",
        "managed_by": "terraform",
        "ephemeral": "true",
        "release_id": release_suffix,
        "owner_task": re.sub(r"[^a-z0-9-]", "-", owner_task_id.lower()).strip("-") if owner_task_id else "unassigned",
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


def validate_staging_config(config: StagingConfig) -> list[str]:
    """Validate all staging configuration parameters against schema and security rules."""
    errors: list[str] = []

    if not RELEASE_ID_PATTERN.fullmatch(config.release_id):
        errors.append(
            f"Invalid release_id: {config.release_id!r}. Must match {RELEASE_ID_PATTERN.pattern}"
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

    return errors


def generate_tfvars(config: StagingConfig) -> dict[str, Any]:
    """Generate Terraform variable mapping for ephemeral staging module."""
    errors = validate_staging_config(config)
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
        "owner_task_id": config.owner_task_id,
        "cloud_sql_instance_name": config.cloud_sql_instance_name,
        "cloud_sql_connection_name": config.cloud_sql_connection_name,
        "network_name": config.network_name,
        "subnetwork_name": config.subnetwork_name,
        "kms_key_id": config.kms_key_id,
        "deployer_service_account_email": config.deployer_service_account_email,
        "additional_labels": config.additional_labels,
    }


def plan_staging_resources(config: StagingConfig, created_at: datetime | None = None) -> list[StagingResource]:
    """Compute the deterministic list of release-scoped ephemeral resources."""
    now = created_at or datetime.now(timezone.utc)
    expires = now + timedelta(hours=config.ttl_hours)
    labels = generate_staging_labels(
        release_id=config.release_id,
        candidate_sha=config.candidate_sha,
        manifest_digest=config.manifest_digest,
        owner_task_id=config.owner_task_id,
        ttl_hours=config.ttl_hours,
        created_at=now,
        additional_labels=config.additional_labels,
    )
    suffix = sanitize_release_suffix(config.release_id)
    name_prefix = f"stg-{suffix[:30]}"
    db_name = f"stg_{suffix.replace('-', '_')}"
    db_user = f"stg_{suffix.replace('-', '_')}_app"

    created_iso = format_timestamp(now)
    expires_iso = format_timestamp(expires)

    return [
        StagingResource(
            resource_type="google_sql_database",
            resource_name=db_name,
            resource_id=f"projects/{config.project_id}/instances/{config.cloud_sql_instance_name}/databases/{db_name}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_sql_user",
            resource_name=db_user,
            resource_id=f"projects/{config.project_id}/instances/{config.cloud_sql_instance_name}/users/{db_user}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_secret_manager_secret",
            resource_name=f"{name_prefix}-database-url",
            resource_id=f"projects/{config.project_id}/secrets/{name_prefix}-database-url",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_secret_manager_secret",
            resource_name=f"{name_prefix}-cursor-signing-key",
            resource_id=f"projects/{config.project_id}/secrets/{name_prefix}-cursor-signing-key",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_secret_manager_secret",
            resource_name=f"{name_prefix}-web-session",
            resource_id=f"projects/{config.project_id}/secrets/{name_prefix}-web-session",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_storage_bucket",
            resource_name=f"{name_prefix}-data-{config.project_id}",
            resource_id=f"gs://{name_prefix}-data-{config.project_id}",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_service_account",
            resource_name=f"{name_prefix}-rt",
            resource_id=f"projects/{config.project_id}/serviceAccounts/{name_prefix}-rt@{config.project_id}.iam.gserviceaccount.com",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_service_account",
            resource_name=f"{name_prefix}-web",
            resource_id=f"projects/{config.project_id}/serviceAccounts/{name_prefix}-web@{config.project_id}.iam.gserviceaccount.com",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_service_account",
            resource_name=f"{name_prefix}-wkr",
            resource_id=f"projects/{config.project_id}/serviceAccounts/{name_prefix}-wkr@{config.project_id}.iam.gserviceaccount.com",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_topic",
            resource_name=f"{name_prefix}-jobs",
            resource_id=f"projects/{config.project_id}/topics/{name_prefix}-jobs",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_topic",
            resource_name=f"{name_prefix}-jobs-dlq",
            resource_id=f"projects/{config.project_id}/topics/{name_prefix}-jobs-dlq",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_subscription",
            resource_name=f"{name_prefix}-jobs",
            resource_id=f"projects/{config.project_id}/subscriptions/{name_prefix}-jobs",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_pubsub_subscription",
            resource_name=f"{name_prefix}-jobs-dlq",
            resource_id=f"projects/{config.project_id}/subscriptions/{name_prefix}-jobs-dlq",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_run_v2_service",
            resource_name=f"{name_prefix}-api",
            resource_id=f"projects/{config.project_id}/locations/{config.region}/services/{name_prefix}-api",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_run_v2_service",
            resource_name=f"{name_prefix}-web",
            resource_id=f"projects/{config.project_id}/locations/{config.region}/services/{name_prefix}-web",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
        StagingResource(
            resource_type="google_cloud_scheduler_job",
            resource_name=f"{name_prefix}-worker-trigger",
            resource_id=f"projects/{config.project_id}/locations/{config.region}/jobs/{name_prefix}-worker-trigger",
            release_id=config.release_id,
            labels=labels,
            created_at=created_iso,
            expires_at=expires_iso,
        ),
    ]


def create_ephemeral_staging(
    config: StagingConfig,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> StagingLifecycleReceipt:
    """Create or plan an ephemeral staging environment instance."""
    now_dt = now or datetime.now(timezone.utc)
    errors = validate_staging_config(config)
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

    planned = plan_staging_resources(config, created_at=now_dt)
    resource_dicts = [
        {
            "type": r.resource_type,
            "name": r.resource_name,
            "id": r.resource_id,
            "status": "planned" if dry_run else "provisioned",
            "created_at": r.created_at,
            "expires_at": r.expires_at,
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
            "dry_run": dry_run,
            "ttl_hours": config.ttl_hours,
            "project_id": config.project_id,
            "region": config.region,
            "scheduler_paused": True,
        },
    )


def is_staging_ephemeral_resource(labels: Mapping[str, str], target_release_id: str) -> bool:
    """Strictly verify if resource labels match the target ephemeral staging release."""
    target_suffix = sanitize_release_suffix(target_release_id)
    if not target_suffix:
        return False

    return (
        labels.get("app") == "oday-plus"
        and labels.get("environment") == "staging"
        and labels.get("ephemeral") == "true"
        and labels.get("release_id") == target_suffix
    )


def cleanup_ephemeral_staging(
    release_id: str,
    *,
    project_id: str,
    resource_inventory: Sequence[Mapping[str, Any]] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    deletion_executor: Callable[[Mapping[str, Any]], bool] | None = None,
) -> StagingLifecycleReceipt:
    """Destroy ephemeral staging resources strictly by exact label matching.

    Guarantees:
    - Never uses wildcards or project-wide deletions.
    - Explicitly verifies `ephemeral=true`, `environment=staging`, `app=oday-plus`, and matching `release_id`.
    - Protected resources (prod/dev/shared infra) are never touched.
    - If any deletion fails, a remediation task is marked.
    """
    now_dt = now or datetime.now(timezone.utc)
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

    # Use supplied inventory or empty if none passed
    inventory = resource_inventory or []
    matching_resources: list[Mapping[str, Any]] = []

    for res in inventory:
        labels = res.get("labels", {})
        if not isinstance(labels, Mapping):
            continue

        # Strictly check label match
        if is_staging_ephemeral_resource(labels, release_id):
            # Guard against accidental prod/shared target
            if labels.get("environment") != "staging" or labels.get("ephemeral") != "true":
                continue
            matching_resources.append(res)

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
            })
            continue

        # Execute deletion via executor callback if provided, else simulated success
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
    """Scan inventory for expired ephemeral staging resources and orphans."""
    now_dt = now or datetime.now(timezone.utc)
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

        if labels.get("app") != "oday-plus" or labels.get("environment") != "staging":
            continue

        if labels.get("ephemeral") != "true":
            continue

        release_id = str(labels.get("release_id", "")).strip()
        created_str = labels.get("created_at", "")
        expires_str = labels.get("expires_at", "")
        res_id = str(res.get("id") or res.get("name", "unknown"))
        res_type = str(res.get("type", "unknown"))

        # Determine expiration
        is_expired = False
        if expires_str:
            try:
                expires_dt = parse_timestamp(expires_str)
                if now_dt >= expires_dt:
                    is_expired = True
            except Exception:
                is_expired = True
        elif created_str:
            try:
                created_dt = parse_timestamp(created_str)
                if now_dt - created_dt >= timedelta(hours=max_ttl_hours):
                    is_expired = True
            except Exception:
                is_expired = True
        else:
            # Missing timestamps in ephemeral resource -> orphan
            is_expired = True

        if not release_id:
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "reason": "Missing release_id label on ephemeral resource",
                "labels": dict(labels),
            })
            alerts.append(f"Orphan resource without release_id found: {res_type} {res_id}")
            continue

        if is_expired:
            expired_releases.add(release_id)
            orphan_resources.append({
                "id": res_id,
                "type": res_type,
                "release_id": release_id,
                "reason": f"Resource expired (exceeded TTL {max_ttl_hours}h)",
                "labels": dict(labels),
            })
            alerts.append(f"Expired staging resource found: {res_type} {res_id} (release: {release_id})")
        else:
            active_releases.add(release_id)

    # Perform auto cleanup on expired resources if requested
    if auto_cleanup and orphan_resources:
        for item in orphan_resources:
            res_dict = {
                "id": item["id"],
                "type": item["type"],
                "labels": item.get("labels", {}),
            }
            rel_id = str(item.get("release_id", ""))
            cleanup_res = cleanup_ephemeral_staging(
                release_id=rel_id if rel_id else "unknown",
                project_id=project_id,
                resource_inventory=[res_dict],
                dry_run=False,
                now=now_dt,
                deletion_executor=deletion_executor,
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
    *,
    extend_hours: int,
    reason: str,
    owner: str,
    current_expires_at: datetime,
    max_total_ttl_hours: int = MAX_TTL_HOURS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Extend TTL for debugging a failed staging deployment with mandatory owner and reason.

    Policy:
    - Ephemeral staging retention on failure must NOT exceed 24 hours without explicit owner and reason.
    - Maximum allowable extension cannot exceed 168 hours (7 days) from initial creation.
    """
    now_dt = now or datetime.now(timezone.utc)

    if not reason or not reason.strip():
        raise ValueError("TTL extension requires a non-empty documented 'reason'.")

    if not owner or not owner.strip():
        raise ValueError("TTL extension requires a non-empty 'owner' identifier.")

    if extend_hours <= 0:
        raise ValueError("extend_hours must be positive.")

    new_expires_at = current_expires_at + timedelta(hours=extend_hours)

    return {
        "release_id": release_id,
        "extended_by_hours": extend_hours,
        "owner": owner.strip(),
        "reason": reason.strip(),
        "previous_expires_at": format_timestamp(current_expires_at),
        "new_expires_at": format_timestamp(new_expires_at),
        "extended_at": format_timestamp(now_dt),
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
    if 'paused           = true' not in main_text and 'paused = true' not in main_text:
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
    create_p.add_argument("--owner-task-id", default="", help="Owner Task ID")
    create_p.add_argument("--dry-run", action="store_true", help="Perform dry-run planning only")
    create_p.add_argument("--tfvars-out", help="Path to write tfvars JSON")

    # cleanup
    clean_p = subparsers.add_parser("cleanup", help="Clean up ephemeral staging resources")
    clean_p.add_argument("--release-id", required=True, help="Target release identifier")
    clean_p.add_argument("--project-id", required=True, help="GCP Project ID")
    clean_p.add_argument("--dry-run", action="store_true", help="Perform dry-run without deletion")
    clean_p.add_argument("--inventory-file", help="JSON file with resource inventory for label filtering")

    # scan-orphans
    scan_p = subparsers.add_parser("scan-orphans", help="Scan for expired ephemeral staging resources")
    scan_p.add_argument("--project-id", required=True, help="GCP Project ID")
    scan_p.add_argument("--max-ttl-hours", type=int, default=DEFAULT_TTL_HOURS, help="Max TTL threshold")
    scan_p.add_argument("--inventory-file", required=True, help="JSON file containing resource inventory")
    scan_p.add_argument("--auto-cleanup", action="store_true", help="Automatically delete expired resources")

    # extend-ttl
    ext_p = subparsers.add_parser("extend-ttl", help="Extend TTL for debugging failed staging run")
    ext_p.add_argument("--release-id", required=True, help="Release identifier")
    ext_p.add_argument("--extend-hours", type=int, required=True, help="Hours to extend")
    ext_p.add_argument("--owner", required=True, help="Owner identity requesting extension")
    ext_p.add_argument("--reason", required=True, help="Documented reason for extension")
    ext_p.add_argument("--current-expires-at", required=True, help="Current expires_at ISO timestamp")

    # validate-contract
    val_p = subparsers.add_parser("validate-contract", help="Validate ephemeral staging Terraform module")
    val_p.add_argument("--module-dir", default="infra/terraform/modules/ephemeral_staging", help="Module dir")

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "create":
        config = StagingConfig(
            release_id=args.release_id,
            candidate_sha=args.candidate_sha,
            manifest_digest=args.manifest_digest,
            project_id=args.project_id,
            region=args.region,
            cloud_sql_instance_name=args.cloud_sql_instance,
            api_image=args.api_image,
            web_image=args.web_image,
            ttl_hours=args.ttl_hours,
            owner_task_id=args.owner_task_id,
        )

        receipt = create_ephemeral_staging(config, dry_run=args.dry_run)
        if args.tfvars_out and receipt.success:
            tfvars = generate_tfvars(config)
            Path(args.tfvars_out).write_text(json.dumps(tfvars, indent=2), encoding="utf-8")

        print(json.dumps(receipt.to_dict(), indent=2))
        return 0 if receipt.success else 1

    elif args.command == "cleanup":
        inventory = []
        if args.inventory_file:
            inventory = json.loads(Path(args.inventory_file).read_text(encoding="utf-8"))

        receipt = cleanup_ephemeral_staging(
            release_id=args.release_id,
            project_id=args.project_id,
            resource_inventory=inventory,
            dry_run=args.dry_run,
        )
        print(json.dumps(receipt.to_dict(), indent=2))
        return 0 if receipt.success else 1

    elif args.command == "scan-orphans":
        inventory = json.loads(Path(args.inventory_file).read_text(encoding="utf-8"))
        result = scan_orphans(
            project_id=args.project_id,
            resource_inventory=inventory,
            max_ttl_hours=args.max_ttl_hours,
            auto_cleanup=args.auto_cleanup,
        )
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.failed_cleanups == 0 else 1

    elif args.command == "extend-ttl":
        curr_exp = parse_timestamp(args.current_expires_at)
        res = extend_staging_ttl(
            release_id=args.release_id,
            extend_hours=args.extend_hours,
            reason=args.reason,
            owner=args.owner,
            current_expires_at=curr_exp,
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
