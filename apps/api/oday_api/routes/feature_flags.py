from __future__ import annotations

from datetime import date
from typing import Any

from shared.audit import AuditEvent, InMemoryAuditLog
from shared.auth.feature_flags import (
    FeatureFlag,
    FeatureFlagRegistry,
    Readiness,
    default_registry,
)

try:
    from fastapi import APIRouter, HTTPException, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:

    class EnableFlagPayload(BaseModel):
        approvals: list[str] = Field(default_factory=list)

    class ApproveFlagPayload(BaseModel):
        approver: str = Field(min_length=1)

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
    ) -> APIRouter:
        router = APIRouter(tags=["admin-feature-flags"])
        flag_registry = registry or default_registry()
        active_audit_log = audit_log or InMemoryAuditLog()

        @router.get("/admin/feature-flags")
        def list_admin_feature_flags() -> dict[str, Any]:
            flags = [f.to_dict() for f in flag_registry.all()]
            return {"status": "ok", "count": len(flags), "flags": flags}

        @router.get("/feature-flags")
        def list_public_feature_flags() -> dict[str, Any]:
            flags = [f.to_dict() for f in flag_registry.all()]
            return {"status": "ok", "count": len(flags), "flags": flags}

        @router.get("/admin/feature-flags/{key}")
        def get_feature_flag(key: str) -> dict[str, Any]:
            flag = flag_registry.get(key)
            if flag is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"feature flag {key!r} not found",
                )
            return {"status": "ok", "flag": flag.to_dict()}

        @router.post("/admin/feature-flags/{key}/enable")
        def enable_feature_flag(key: str, body: EnableFlagPayload | None = None) -> dict[str, Any]:
            approvals = frozenset(body.approvals if body else [])
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

            active_audit_log.record(
                AuditEvent(
                    event_type="feature_flag.enable",
                    actor=updated.owner,
                    action="enable",
                    resource=f"feature_flag:{key}",
                    outcome="allow",
                    correlation_id="corr-feature-flag",
                    metadata={"key": key, "approvals": list(updated.approved_by)},
                )
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags/{key}/disable")
        def disable_feature_flag(key: str) -> dict[str, Any]:
            try:
                updated = flag_registry.disable(key)
            except KeyError as err:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"feature flag {key!r} not found",
                ) from err

            active_audit_log.record(
                AuditEvent(
                    event_type="feature_flag.disable",
                    actor=updated.owner,
                    action="disable",
                    resource=f"feature_flag:{key}",
                    outcome="allow",
                    correlation_id="corr-feature-flag",
                    metadata={"key": key, "reason": "kill_switch_engaged"},
                )
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags/{key}/approve")
        def approve_feature_flag(key: str, body: ApproveFlagPayload) -> dict[str, Any]:
            try:
                updated = flag_registry.add_approval(key, body.approver)
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

            active_audit_log.record(
                AuditEvent(
                    event_type="feature_flag.approve",
                    actor=body.approver,
                    action="approve",
                    resource=f"feature_flag:{key}",
                    outcome="allow",
                    correlation_id="corr-feature-flag",
                    metadata={"key": key, "approver": body.approver},
                )
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags")
        def register_feature_flag(body: RegisterFlagPayload) -> dict[str, Any]:
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

            active_audit_log.record(
                AuditEvent(
                    event_type="feature_flag.register",
                    actor=body.owner,
                    action="register",
                    resource=f"feature_flag:{body.key}",
                    outcome="allow",
                    correlation_id="corr-feature-flag",
                    metadata={"key": body.key},
                )
            )
            return {"status": "ok", "flag": flag.to_dict()}

        return router
