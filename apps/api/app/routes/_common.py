"""Shared HTTP helpers used by product route factories.

These helpers intentionally contain no service-specific behavior. Keeping the
tenant boundary and fail-closed runtime response in one place prevents route
modules from drifting into subtly different authorization semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status


def resolve_tenant_id(request: Request) -> str:
    """Resolve and verify the tenant scope for the current request."""
    principal = getattr(request.state, "operator_principal", None)
    if principal is None:
        from apps.api.oday_api.security.dependencies import principal_from_headers

        try:
            principal = principal_from_headers(request.headers)
        except Exception:
            principal = None

    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TENANT_SCOPE_DENIED: Missing verified principal",
        )

    principal_tenant = getattr(getattr(principal, "scope", None), "tenant_id", None) or getattr(
        principal, "tenant_id", None
    )
    if not principal_tenant or not str(principal_tenant).strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TENANT_SCOPE_DENIED: Missing verified tenant scope",
        )
    clean_tenant = str(principal_tenant).strip()

    header_tenant = (
        request.headers.get("x-tenant-id") or request.headers.get("tenant_id") or ""
    ).strip()
    if header_tenant and header_tenant != clean_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TENANT_SCOPE_DENIED: Tenant header does not match verified principal scope",
        )
    return clean_tenant


def runtime_binding_guard(composition_error: Any) -> Callable[[], None]:
    """Build a FastAPI dependency that fails closed on bad runtime wiring."""

    def require_runtime_binding() -> None:
        if composition_error is not None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": composition_error.code,
                    "message": str(composition_error),
                },
            )

    return require_runtime_binding


def durable_store_required(message: str) -> HTTPException:
    """Return the standard response for a missing durable command store."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "DURABLE_COMMAND_STORE_REQUIRED",
            "message": message,
        },
    )


def reset_allowed_guard(*, allow_reset: bool, resource_label: str) -> Callable[[], None]:
    """Build the shared fail-closed dependency for product reset endpoints."""

    def require_reset_allowed() -> None:
        if not allow_reset:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PRODUCTION_RESET_DENIED",
                    "message": f"{resource_label} reset is disabled in live mode",
                },
            )

    return require_reset_allowed


def read_operator_context(
    request: Request,
    *,
    x_operator_role: str | None,
    x_subject_id: str | None,
    x_roles: str | None,
) -> dict[str, str | None]:
    """Merge verified request state with legacy operator headers."""
    return {
        "role_id": getattr(request.state, "operator_role_id", None) or x_operator_role,
        "subject_id": getattr(request.state, "operator_subject_id", None) or x_subject_id,
        "system_roles": getattr(request.state, "operator_system_roles", None) or x_roles,
    }
