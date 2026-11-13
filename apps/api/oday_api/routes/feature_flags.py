from __future__ import annotations

import os
from datetime import date
from threading import Lock
from typing import Any
from uuid import UUID

from modules.opsboard.auth import AuthenticationBoundary
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.auth import Action, AuthorizationEngine, Principal
from shared.auth.feature_flags import (
    FeatureFlag,
    FeatureFlagConfigurationConflict,
    FeatureFlagRegistry,
    FlagScope,
    FlagScopeBinding,
    Readiness,
    default_registry,
)

try:
    from fastapi import APIRouter, Depends, HTTPException, Request, status
    from pydantic import (
        BaseModel,
        ConfigDict,
        Field,
        StrictBool,
        StrictInt,
        StrictStr,
        model_validator,
    )
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:

    class FlagCommandProof(BaseModel):
        reason: StrictStr
        riskAcknowledged: StrictBool

        @model_validator(mode="before")
        @classmethod
        def safe_raw_proof(cls, value):
            # Invalid proof must not enter RequestValidationError.input, which
            # can retain raw JSON NaN/Infinity and break its JSON renderer.
            # Keep this local; no global error-handler or parser change.
            if not isinstance(value, dict):
                raise HTTPException(status_code=422, detail="FEATURE_FLAG_COMMAND_BODY_INVALID")
            reason = value.get("reason")
            if not isinstance(reason, str):
                raise HTTPException(status_code=422, detail="FEATURE_FLAG_REASON_INVALID")
            acknowledged = value.get("riskAcknowledged")
            if type(acknowledged) is not bool:
                raise HTTPException(status_code=422, detail="FEATURE_FLAG_RISK_ACKNOWLEDGMENT_REQUIRED")
            return value

    class EnableFlagPayload(FlagCommandProof):
        approvals: list[str] = Field(default_factory=list)
        configuration_version: StrictInt | None = Field(default=None, ge=1)

    class ApproveFlagPayload(FlagCommandProof):
        approver: str | None = Field(default=None, min_length=1)
        configuration_version: StrictInt | None = Field(default=None, ge=1)

    class ScopeBindingPayload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        tenant_id: UUID
        source_id: StrictStr | None = None

    class ConfigureFlagScopePayload(FlagCommandProof):
        model_config = ConfigDict(extra="forbid")
        configuration_version: StrictInt = Field(ge=1)
        bindings: list[ScopeBindingPayload]

    class RegisterFlagPayload(FlagCommandProof):
        key: str = Field(min_length=1)
        owner: str = Field(min_length=1)
        description: str = ""
        high_risk: bool = False
        readiness: str = "experimental"
        expires_on: str | None = None
        scope: FlagScope = FlagScope.GLOBAL
        bindings: list[ScopeBindingPayload] = Field(default_factory=list)

    def create_feature_flags_router(
        *,
        registry: FeatureFlagRegistry | None = None,
        audit_log: InMemoryAuditLog | None = None,
        engine: AuthorizationEngine | None = None,
        boundary: AuthenticationBoundary | None = None,
        queue: Any = None,
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
        from shared.infrastructure.persistence.feature_flags import (
            DurableFeatureFlagRegistry,
            FeatureFlagStoreUnavailable,
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
                not isinstance(flag_registry, DurableFeatureFlagRegistry)
                and request_boundary() is None
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
                principal = guard(request)
                if action in (Action.CREATE, Action.UPDATE):
                    # FastAPI resolves this dependency before body validation:
                    # real auth/RBAC and owner availability retain precedence.
                    read_owner("all")
                return principal
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

        def read_owner(method: str, *args):
            if live_data_required() and not isinstance(flag_registry, DurableFeatureFlagRegistry):
                raise HTTPException(status_code=503, detail="FEATURE_FLAG_STORE_UNAVAILABLE")
            try:
                return getattr(flag_registry, method)(*args)
            except FeatureFlagStoreUnavailable as exc:
                raise HTTPException(status_code=503, detail="FEATURE_FLAG_STORE_UNAVAILABLE") from exc

        def command_proof(body: FlagCommandProof | None, operation: str) -> dict[str, Any]:
            # Authentication/RBAC have already run. A missing or broken real
            # owner remains unavailable; a UI declaration never substitutes it.
            read_owner("all")
            reason = body.reason.strip() if body is not None and body.reason is not None else ""
            if not reason:
                raise HTTPException(status_code=422, detail="FEATURE_FLAG_REASON_REQUIRED")
            if operation == "approve" and len(reason) < 10:
                raise HTTPException(status_code=422, detail="FEATURE_FLAG_APPROVAL_REASON_TOO_SHORT")
            if body is None or body.riskAcknowledged is not True:
                raise HTTPException(status_code=422, detail="FEATURE_FLAG_RISK_ACKNOWLEDGMENT_REQUIRED")
            # This is the caller's explicit command confirmation. It is not a
            # second authenticated principal or a human release signoff.
            return {"reason": reason, "riskAcknowledged": True}

        def durable_command(operation: str, key: str, request: Request, principal: Principal,
                            *, proof: dict[str, Any], flag: FeatureFlag | None = None,
                            configuration_version: int | None = None,
                            bindings: tuple[FlagScopeBinding, ...] | None = None):
            from shared.infrastructure.persistence.feature_flags import (
                DurableFeatureFlagRegistry,
                FeatureFlagAlreadyExists,
                FeatureFlagStoreUnavailable,
                FeatureFlagWriteConflict,
                UnavailableFeatureFlagRegistry,
            )
            if isinstance(flag_registry, UnavailableFeatureFlagRegistry):
                raise HTTPException(status_code=503, detail="FEATURE_FLAG_STORE_UNAVAILABLE")
            if not isinstance(flag_registry, DurableFeatureFlagRegistry):
                if live_data_required():
                    raise HTTPException(status_code=503, detail="FEATURE_FLAG_STORE_UNAVAILABLE")
                return None
            try:
                return flag_registry.execute(
                    operation, key, actor=principal.subject_id,
                    correlation_id=_correlation_id_from_request(request),
                    audit_log=active_audit_log, queue=queue, flag=flag,
                    reason=proof["reason"], risk_acknowledged=proof["riskAcknowledged"],
                    configuration_version=configuration_version, bindings=bindings,
                )
            except FeatureFlagAlreadyExists as exc:
                raise HTTPException(status_code=409, detail="feature flag already exists") from exc
            except FeatureFlagStoreUnavailable as exc:
                raise HTTPException(status_code=503, detail="FEATURE_FLAG_STORE_UNAVAILABLE") from exc
            except FeatureFlagWriteConflict as exc:
                raise HTTPException(status_code=409, detail="FEATURE_FLAG_VERSION_CONFLICT") from exc
            except FeatureFlagConfigurationConflict as exc:
                raise HTTPException(status_code=409, detail="FEATURE_FLAG_CONFIGURATION_CONFLICT") from exc

        def command_response(result):
            return {"status": "ok", "flag": result.flag.to_dict(),
                    "audit": {"event_id": result.audit_event_id,
                              "delivery_status": result.audit_delivery}}

        @router.get("/admin/feature-flags", dependencies=[Depends(view_guard)])
        def list_admin_feature_flags() -> dict[str, Any]:
            flags = [f.to_dict() for f in read_owner("all")]
            return {"status": "ok", "count": len(flags), "flags": flags}

        @router.get("/feature-flags")
        def list_public_feature_flags() -> dict[str, Any]:
            # Runtime callers still read the same effective policy; this is
            # not an administration or mutation permission.
            flags = [f.to_dict(include_bindings=False) for f in read_owner("all")]
            return {"status": "ok", "count": len(flags), "flags": flags}

        @router.get("/admin/feature-flags/{key}", dependencies=[Depends(view_guard)])
        def get_feature_flag(key: str) -> dict[str, Any]:
            flag = read_owner("get", key)
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
            body: EnableFlagPayload,
            principal: Principal = update_dependency,
        ) -> dict[str, Any]:
            if body and body.approvals and not fixture_command_payloads_allowed():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="approvals must be recorded by authenticated principals",
                )
            proof = command_proof(body, "enable")
            approvals = (
                frozenset(body.approvals)
                if body and fixture_command_payloads_allowed()
                else frozenset()
            )
            try:
                result = durable_command("enable", key, request, principal, proof=proof,
                                         configuration_version=body.configuration_version)
                if result is not None:
                    return command_response(result)
                updated = flag_registry.enable(key, approvals=approvals,
                                               configuration_version=body.configuration_version)
            except FeatureFlagConfigurationConflict as exc:
                raise HTTPException(status_code=409, detail="FEATURE_FLAG_CONFIGURATION_CONFLICT") from exc
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
                metadata={"key": key, "approvals": list(updated.approved_by), **proof},
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags/{key}/disable")
        def disable_feature_flag(
            key: str,
            request: Request,
            body: FlagCommandProof,
            principal: Principal = update_dependency,
        ) -> dict[str, Any]:
            proof = command_proof(body, "disable")
            try:
                result = durable_command("disable", key, request, principal, proof=proof)
                if result is not None:
                    return command_response(result)
                updated = flag_registry.disable(key)
            except KeyError as err:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"feature flag {key!r} not found",
                ) from err
            record(
                request, principal, action="disable", key=key,
                metadata={"key": key, "kill_switch_engaged": True, **proof},
            )
            return {"status": "ok", "flag": updated.to_dict()}

        @router.post("/admin/feature-flags/{key}/approve")
        def approve_feature_flag(
            key: str,
            request: Request,
            body: ApproveFlagPayload,
            principal: Principal = update_dependency,
        ) -> dict[str, Any]:
            if body and body.approver is not None and not fixture_command_payloads_allowed():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="approver is the authenticated principal, not a request field",
                )
            proof = command_proof(body, "approve")
            approver = (
                body.approver
                if body and body.approver is not None and fixture_command_payloads_allowed()
                else principal.subject_id
            )
            try:
                result = durable_command("approve", key, request, principal, proof=proof,
                                         configuration_version=body.configuration_version)
                if result is not None:
                    return command_response(result)
                updated = flag_registry.add_approval(key, approver,
                                                     configuration_version=body.configuration_version)
            except FeatureFlagConfigurationConflict as exc:
                raise HTTPException(status_code=409, detail="FEATURE_FLAG_CONFIGURATION_CONFLICT") from exc
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
                metadata={"key": key, "approver": approver, **proof},
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
                if read_owner("get", body.key) is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="feature flag already exists",
                    )
                proof = command_proof(body, "register")
                try:
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
                        scope=body.scope,
                        bindings=tuple(FlagScopeBinding.from_dict(v.model_dump(mode="json")) for v in body.bindings),
                    )
                except (ValueError, TypeError) as exc:
                    raise HTTPException(status_code=422, detail="FEATURE_FLAG_CONFIGURATION_INVALID") from exc
                result = durable_command("register", flag.key, request, principal, proof=proof, flag=flag)
                if result is not None:
                    return command_response(result)
                flag_registry.register(flag)
            record(
                request, principal, action="register", key=body.key,
                metadata={"key": body.key, **proof},
            )
            return {"status": "ok", "flag": flag.to_dict()}

        @router.post("/admin/feature-flags/{key}/scope")
        def configure_feature_flag_scope(
            key: str,
            body: ConfigureFlagScopePayload,
            request: Request,
            principal: Principal = update_dependency,
        ) -> dict[str, Any]:
            proof = command_proof(body, "configure_scope")
            try:
                bindings = tuple(FlagScopeBinding.from_dict(v.model_dump(mode="json")) for v in body.bindings)
                result = durable_command("configure_scope", key, request, principal, proof=proof,
                    bindings=bindings, configuration_version=body.configuration_version)
                if result is not None:
                    return command_response(result)
                before = flag_registry.get(key)
                updated = flag_registry.configure_scope(key, bindings,
                    configuration_version=body.configuration_version)
            except FeatureFlagConfigurationConflict as exc:
                raise HTTPException(status_code=409, detail="FEATURE_FLAG_CONFIGURATION_CONFLICT") from exc
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="feature flag not found") from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=422, detail="FEATURE_FLAG_CONFIGURATION_INVALID") from exc
            record(request, principal, action="configure_scope", key=key,
                   metadata={"key": key, "before": before.to_dict() if before else None,
                             "after": updated.to_dict(), **proof})
            return {"status": "ok", "flag": updated.to_dict()}

        return router
