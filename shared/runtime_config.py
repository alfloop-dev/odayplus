"""Repository-wide runtime configuration and release identity primitives."""

from __future__ import annotations

import os


def get_release_identity(default: str = "") -> str:
    """Resolve the authoritative release identity across all runtime roles.

    Search order:
    1. ``ODAY_RELEASE_SHA`` (Primary deploy contract)
    2. ``ODP_RELEASE_COMMIT_SHA`` (Compatibility fallback)
    3. ``RELEASE_SHA`` (Generic release identity)
    4. ``GITHUB_SHA`` (CI/CD build context)
    5. ``COMMIT_SHA`` (Alternative VCS context)
    """
    return (
        os.environ.get("ODAY_RELEASE_SHA")
        or os.environ.get("ODP_RELEASE_COMMIT_SHA")
        or os.environ.get("RELEASE_SHA")
        or os.environ.get("GITHUB_SHA")
        or os.environ.get("COMMIT_SHA")
        or default
    ).strip()


def resolve_tenant_id(*, required: bool = False, default: str = "tenant-dev") -> str:
    """Resolve the tenant ID for scheduled ingestion and worker tasks."""
    tenant = (
        os.environ.get("ODP_SCHEDULED_INGESTION_TENANT_ID")
        or os.environ.get("ODP_TENANT_ID")
        or ""
    ).strip()
    if not tenant:
        if required:
            raise ValueError(
                "Tenant identity required: neither ODP_SCHEDULED_INGESTION_TENANT_ID nor ODP_TENANT_ID is set"
            )
        return default
    return tenant
