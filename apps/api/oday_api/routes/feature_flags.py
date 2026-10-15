from __future__ import annotations

import os
from datetime import date
from threading import Lock
from typing import Any

from modules.opsboard.auth import AuthenticationBoundary
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.auth import Action, AuthorizationEngine, Principal
from shared.auth.feature_flags import (
    FeatureFlag,
    FeatureFlagRegistry,
    Readiness,
    default_registry,
)

try:
    from fastapi import APIRouter, Depends, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:

    class EnableFlagPayload(BaseModel):
        approvals: list[str] = Field(default_factory=list)

    class ApproveFlagPayload(BaseModel):
        approver: str | None = Field(default=None, min_length=1)

    class RegisterFlagPayload(BaseModel):
        key: str = Field(min_length=1)
        owner: str = Field(min_length=1)
        description: str = ""
        high_risk: bool = False
        readiness: str = "experimental"
        expires_on: str | None = None

    def create_feature_flags_router(
        *,
        registry: FeatureFlagRegistry | None = None,
        audit_log: InMemoryAuditLog | None = None,
        engine: AuthorizationEngine | None = None,
        boundary: AuthenticationBoundary | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.runtime_mode import deployment_mode, live_data_required
        from apps.api.oday_api.security.dependencies import (
            _correlation_id_from_request,
            build_engine,
            default_boundary,
            require_permission,
        )

        router = APIRouter(tags=["admin-feature-flags"])
        flag_registry = registry or default_registry()
        active_audit_log = audit_log or InMemoryAuditLog()
        active_engine = engine or build_engine(
            audit_log=active_audit_log, flags=flag_registry
        )
        registration_lock = Lock()
        selected_boundary = boundary if boundary is not None else default_boundary()

        def request_boundary() -> AuthenticationBoundary | None:
            return selected_boundary

        def local_fixture_identity_allowed() -> bool:
            # Header identity/body names are solely the existing, explicit
            # local POC fixture lane. Data readiness alone grants no exception.
            return (
                os.environ.get("ODP_PRODUCT_MODE", "").strip().lower() == "poc"
                and deployment_mode() in {
                    "dev", "development", "local", "test", "testing", "e2e"
                }
                and not live_data_required()
                and not os.environ.get("ODP_RELEASE_PROFILE", "").strip()
                and not os.environ.get("ODP_AUTH_MODE", "").strip()
                and not os.environ.get("ODP_AUTH_OIDC_ENABLED", "").strip()
            )

        def fixture_command_payloads_allowed() -> bool:
            # A configured boundary remains authoritative even in dev-admin or
            # when business data is not ready. Client fields cannot select POC.
            return (
                request_boundary() is None
                and default_boundary() is None
                and local_fixture_identity_allowed()
            )

        def permission(action: Action):
            def dependency(request: Request) -> Principal:
                active_boundary = request_boundary()
                if active_boundary is None and (
                    not local_fixture_identity_allowed() or default_boundary() is not None
                ):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="authenticated feature flag administration requires an auth boundary",
                    )
                guard = require_permission(
                    "feature_flag", action, engine=active_engine, boundary=active_boundary
                )
                return guard(request)
            return dependency

        view_guard = permission(Action.VIEW)
        create_guard = permission(Action.CREATE)
        update_guard = permission(Action.UPDATE)
        create_dependency = Depends(create_guard)
        update_dependency = Depends(update_guard)

        def record(
            request: Request,
            principal: Principal,
            *,
            action: str,
            key: str,
            metadata: dict[str, Any],
        ) -> None:
            active_audit_log.record(
                AuditEvent(
                    event_type=f"feature_flag.{action}",
                    actor=principal.subject_id,
                    action=action,
                    resource=f"feature_flag:{key}",
                    outcome="allow",
                    correlation_id=_correlation_id_from_request(request),
                    metadata=metadata,
                )
            )

        @router.get("/admin/feature-flags", dependencies=[Depends(view_guard)])
        def list_admin_feature_flags() -> dict[str, Any]:
            flags = [f.to_dict() for f in flag_registry.all()]
            return {"status": "ok", "count": len(flags), "flags": flags}

        @router.get("/feature-flags")
        def list_public_feature_flags() -> dict[str, Any]:
            # Runtime callers still read the same effective policy; this is
            # not an administration or mutation permission.
            flags = [f.to_dict() for f in flag_registry.all()]
            return {"status": "ok", "count": len(flags), "flags": flags}

        @router.get("/admin/feature-flags/{key}", dependencies=[Depends(view_guard)])
        def get_feature_flag(key: str) -> dict[str, Any]:
            flag = flag_registry.get(key)
            if flag is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"feature flag {key!r} not found",
                )
            return {"status": "ok", "flag": flag.to_dict()}

        @router.post("/admin/feature-flags/{key}/enable")
        def enable_feature_flag(
            key: str,
            request: Request,
            body: EnableFlagPayload | None = None,
            principal: Principal = update_dependency,
        ) -> dict[str, Any]:
            if body and body.approvals and not fixture_command_payloads_allowed():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="approvals must be recorded by authenticated principals",
                )
            approvals = (
                frozenset(body.approvals)
                if body and fixture_command_payloads_allowed()
                else frozenset()
            )
            try:
                updated = flag_registry.enable(key, approvals=approvals)
            except KeyError as err:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"feature flag {key!r} not found",
                ) from err
            except PermissionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=str(exc),
                ) from exc
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            record(
                request, principal, action="enable", key=key,
                metadata={"key": key, "approvals": list(updated.approved_by)},
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags/{key}/disable")
        def disable_feature_flag(
            key: str,
            request: Request,
            principal: Principal = update_dependency,
        ) -> dict[str, Any]:
            try:
                updated = flag_registry.disable(key)
            except KeyError as err:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"feature flag {key!r} not found",
                ) from err
            record(
                request, principal, action="disable", key=key,
                metadata={"key": key, "reason": "kill_switch_engaged"},
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags/{key}/approve")
        def approve_feature_flag(
            key: str,
            request: Request,
            body: ApproveFlagPayload | None = None,
            principal: Principal = update_dependency,
        ) -> dict[str, Any]:
            if body and body.approver is not None and not fixture_command_payloads_allowed():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="approver is the authenticated principal, not a request field",
                )
            approver = (
                body.approver
                if body and body.approver is not None and fixture_command_payloads_allowed()
                else principal.subject_id
            )
            try:
                updated = flag_registry.add_approval(key, approver)
            except KeyError as err:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"feature flag {key!r} not found",
                ) from err
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            record(
                request, principal, action="approve", key=key,
                metadata={"key": key, "approver": approver},
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags")
        def register_feature_flag(
            body: RegisterFlagPayload,
            request: Request,
            principal: Principal = create_dependency,
        ) -> dict[str, Any]:
            # CREATE cannot replace policy or discard existing approvals. Keep
            # this check and this router's insertion in one critical section.
            with registration_lock:
                if flag_registry.get(body.key) is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="feature flag already exists",
                    )
                expires_on = date.fromisoformat(body.expires_on) if body.expires_on else None
                try:
                    readiness = Readiness(body.readiness)
                except ValueError:
                    readiness = Readiness.EXPERIMENTAL
                flag = FeatureFlag(
                    key=body.key,
                    owner=body.owner,
                    description=body.description,
                    high_risk=body.high_risk,
                    readiness=readiness,
                    expires_on=expires_on,
                )
                flag_registry.register(flag)
            record(
                request, principal, action="register", key=body.key,
                metadata={"key": body.key},
            )
            return {"status": "ok", "flag": flag.to_dict()}

        return router
