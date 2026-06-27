"""API security layer for the ODay Plus core API.

Wires the shared authorization engine (RBAC + ABAC + feature flags + audit)
into FastAPI request handling. FastAPI is an optional import so the package
remains importable in lean environments (mirrors ``oday_api.main``).

Artifact note: the task brief lists this surface as ``apps/api/app/security/``;
the concrete FastAPI application package in this repo is ``oday_api`` (see
``apps/api/oday_api/main.py``), so the security layer lives alongside it.
"""

from .dependencies import (
    AuthorizationError,
    authorize_request,
    build_engine,
    principal_from_headers,
    require_feature_flag,
    require_permission,
)

__all__ = [
    "AuthorizationError",
    "authorize_request",
    "build_engine",
    "principal_from_headers",
    "require_feature_flag",
    "require_permission",
]
