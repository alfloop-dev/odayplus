"""User & Role Management sub-router for Operator Console (ODP-CAP-USER-ROLE-UI-001).

Routes (all under /operator/users):
  GET  /operator/users             — List all user role/scope assignments
  GET  /operator/users/roles       — List canonical platform role definitions
  GET  /operator/users/audit-trail — List user & role management audit trail
  GET  /operator/users/{subject_id} — Get user role detail by subject_id
  POST /operator/users             — Save (create/update) user role & scope mapping
  POST /operator/users/{subject_id}/status — Update user active status
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.opsboard.application.user_role_management import (
    UserNotFound,
    UserRoleManagementService,
    UserRolePolicyError,
)
from shared.auth import Principal
from shared.identity.invitation_service import InvitationRefused, InvitationService

# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------


class ScopePayload(BaseModel):
    """Scope constraint parameters on Tenant, Brand, Region, Store, etc."""

    model_config = ConfigDict(extra="allow")

    tenant_id: str = "tenant-default"
    brand_ids: list[str] = Field(default_factory=list)
    region_ids: list[str] = Field(default_factory=list)
    store_ids: list[str] = Field(default_factory=list)
    assigned_area_ids: list[str] = Field(default_factory=list)
    heat_zone_ids: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    clearance: str = "CONFIDENTIAL"


class UserSavePayload(BaseModel):
    """POST /operator/users — payload for user role & scope assignment."""

    model_config = ConfigDict(extra="allow")

    subjectId: str = Field(min_length=1)
    email: str | None = None
    name: str | None = None
    roles: list[str] = Field(min_length=1)
    scope: ScopePayload | None = None
    attributes: dict[str, Any] | None = None
    status: str = "active"
    reason: str = ""
    actorName: str | None = None
    actorRole: str | None = None


class UserStatusPayload(BaseModel):
    """POST /operator/users/{subject_id}/status — payload for status change."""

    model_config = ConfigDict(extra="allow")

    status: str
    reason: str = ""
    actorName: str | None = None


class UserInvitePayload(BaseModel):
    """POST /operator/users/invite — payload for inviting / creating a new user."""

    model_config = ConfigDict(extra="allow")

    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    name: str | None = None
    displayName: str | None = None
    roles: list[str] = Field(min_length=1)
    scope: ScopePayload | None = None
    initialPassword: str | None = None
    initial_password: str | None = None
    reason: str = ""


class InvitationIssuePayload(BaseModel):
    """No caller-supplied actor, tenant, roles, scope or account identifier."""

    model_config = ConfigDict(extra="forbid", strict=True)
    email: str = Field(min_length=3, max_length=320)
    lifetime_seconds: int = Field(default=3600, ge=1, le=259200)


class InvitationIssuedPayload(BaseModel):
    status: Literal["invited"]
    invitation_id: str
    tenant_id: str
    expires_at: str
    audit_event_id: str
    token: str = Field(repr=False, description="One-time private in-memory custody; never log or persist.")


class InvitationRevokedReceipt(BaseModel):
    status: Literal["revoked"]
    invitation_id: str
    audit_event_id: str


async def _invitation_body(request: Request, *, limit: int = 4096) -> dict[str, Any]:
    # Do not let FastAPI's default validation handler echo input. Even an
    # unexpected field or malformed request could contain a password/token.
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, detail={"code": "INVITATION_JSON_REQUIRED"})
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(413, detail={"code": "INVITATION_BODY_TOO_LARGE"})
    try:
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError()
        return parsed
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, detail={"code": "INVITATION_INPUT_INVALID"}) from None


def _invitation_error(exc: InvitationRefused) -> JSONResponse:
    code = exc.code
    http_status = 403 if code in {
        "INVITATION_ADMIN_REQUIRED", "INVITATION_TENANT_REQUIRED",
        "INVITATION_SESSION_REQUIRED", "INVITATION_ADMIN_SESSION_INVALID",
    } else 429 if code == "INVITATION_RATE_LIMITED" else 404 if code == "INVITATION_NOT_FOUND" else 409 if code in {
        "INVITATION_ACCOUNT_EXISTS", "INVITATION_PENDING_EXISTS", "INVITATION_UNAVAILABLE",
    } else 422
    return JSONResponse({"error": {"code": code}}, status_code=http_status,
                        headers={"cache-control": "no-store"})


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_user_role_sub_router(
    service: UserRoleManagementService,
    *,
    require_view_permission_fn: Any = None,
    require_manage_permission_fn: Any = None,
    service_resolver: Any = None,
) -> APIRouter:
    """Return the User & Role Management sub-router."""
    router = APIRouter(prefix="/users", tags=["operator-users-roles"])

    read_deps: list[Any] = (
        [Depends(require_view_permission_fn)] if require_view_permission_fn else []
    )
    manage_deps: list[Any] = (
        [Depends(require_manage_permission_fn)] if require_manage_permission_fn else []
    )

    def get_svc(req: Request) -> UserRoleManagementService:
        return resolve_service(req, service, service_resolver)

    def caller_tenant(req: Request) -> str | None:
        return (
            getattr(req.state, "operator_tenant_id", None)
            or getattr(req.state, "tenant_id", None)
            or req.headers.get("x-tenant-id")
        )

    def tenant_kwargs(svc: Any, req: Request) -> dict[str, Any]:
        # Identity-backed administration is tenant-scoped on every read, not
        # only on writes; the document service keeps its historical signature.
        if getattr(svc, "requires_tenant", False):
            return {"tenant_id": caller_tenant(req)}
        return {}

    def actor_kwargs(svc: Any, req: Request) -> dict[str, Any]:
        if not getattr(svc, "requires_tenant", False):
            return {}
        raw = getattr(req.state, "operator_system_roles", None) or ""
        return {"actor_roles": frozenset(r for r in str(raw).split(",") if r)}

    @router.get("", dependencies=read_deps)
    def list_users(
        request: Request,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        try:
            users = svc.list_users(status_filter=status_filter, **tenant_kwargs(svc, request))
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        return {
            "users": users,
            "count": len(users),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }

    @router.get("/roles", dependencies=read_deps)
    def list_roles(request: Request) -> dict[str, Any]:
        svc = get_svc(request)
        roles = svc.list_roles()
        return {
            "roles": roles,
            "count": len(roles),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }

    @router.get("/audit-trail", dependencies=read_deps)
    def list_audit_trail(
        request: Request,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        try:
            events = svc.get_audit_trail(subject_id=subject_id, tenant_id=caller_tenant(request))
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        return {
            "events": events,
            "count": len(events),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }

    def invitation_context(request: Request) -> tuple[InvitationService, Principal]:
        # ONLY the existing permission dependency's verified Principal. Never
        # construct one from request headers/body or a selected console persona.
        principal = getattr(request.state, "operator_principal", None)
        if not require_manage_permission_fn or not isinstance(principal, Principal):
            raise HTTPException(403, detail={"code": "INVITATION_ADMIN_REQUIRED"})
        svc = get_svc(request)
        if not getattr(svc, "requires_tenant", False):
            raise HTTPException(503, detail={"code": "IDENTITY_PERSISTENCE_UNAVAILABLE"})
        invitations = svc.invitation_service
        if not isinstance(invitations, InvitationService):
            raise HTTPException(503, detail={"code": "IDENTITY_PERSISTENCE_UNAVAILABLE"})
        return invitations, principal

    @router.post(
        "/invitations", dependencies=manage_deps, status_code=201,
        response_model=InvitationIssuedPayload, operation_id="issueIdentityInvitation",
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": InvitationIssuePayload.model_json_schema()}
        }}},
    )
    async def issue_invitation(request: Request) -> JSONResponse:
        try:
            parsed = await _invitation_body(request)
            try:
                payload = InvitationIssuePayload.model_validate(parsed)
            except ValidationError:
                raise HTTPException(422, detail={"code": "INVITATION_INPUT_INVALID"}) from None
            invitations, principal = invitation_context(request)
            result = await run_in_threadpool(invitations.issue, principal, **payload.model_dump())
            # Capability is returned once to the verified issuer, for private
            # in-memory custody only. Receipt/audit representations exclude it.
            return JSONResponse({**result.to_receipt(), "token": result.token}, status_code=201,
                                headers={"cache-control": "no-store"})
        except InvitationRefused as exc:
            return _invitation_error(exc)
        except HTTPException as exc:
            code = exc.detail.get("code", "INVITATION_INPUT_INVALID") if isinstance(exc.detail, dict) else "INVITATION_INPUT_INVALID"
            return JSONResponse({"error": {"code": code}}, status_code=exc.status_code,
                                headers={"cache-control": "no-store"})
        except Exception:
            return JSONResponse({"error": {"code": "IDENTITY_PERSISTENCE_UNAVAILABLE"}},
                                status_code=503, headers={"cache-control": "no-store"})

    @router.post(
        "/invitations/{invitation_id}/revoke", dependencies=manage_deps,
        response_model=InvitationRevokedReceipt, operation_id="revokeIdentityInvitation",
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": {"type": "object", "additionalProperties": False}}
        }}},
    )
    async def revoke_invitation(invitation_id: str, request: Request) -> JSONResponse:
        try:
            if await _invitation_body(request):
                raise HTTPException(422, detail={"code": "INVITATION_INPUT_INVALID"})
            invitations, principal = invitation_context(request)
            result = await run_in_threadpool(invitations.revoke, principal, invitation_id=invitation_id)
            return JSONResponse(result, headers={"cache-control": "no-store"})
        except InvitationRefused as exc:
            return _invitation_error(exc)
        except HTTPException as exc:
            code = exc.detail.get("code", "INVITATION_INPUT_INVALID") if isinstance(exc.detail, dict) else "INVITATION_INPUT_INVALID"
            return JSONResponse({"error": {"code": code}}, status_code=exc.status_code,
                                headers={"cache-control": "no-store"})
        except Exception:
            return JSONResponse({"error": {"code": "IDENTITY_PERSISTENCE_UNAVAILABLE"}},
                                status_code=503, headers={"cache-control": "no-store"})

    def journal_operation(request: Request, body: dict[str, Any] | None) -> dict[str, Any]:
        # Reuse the existing verified identity and durable session. Never trust
        # headers/body for actor, tenant, source approval or admission authority.
        from datetime import datetime

        from shared.identity.dev_smoke_journal import (
            AUTHORIZATION_ID,
            CREDENTIAL_BUNDLE_SECRET_NAME,
            PRESERVED_ACCOUNT_ID,
            TENANT_ID,
            DevSmokeBindingJournal,
            JournalReservation,
            ProvisioningJournal,
            ProvisioningRefused,
            original_account_readback,
        )
        from shared.identity.invitation_provenance import invitation_provenance

        invitations, principal = invitation_context(request)
        if os.environ.get("ODP_DEPLOY_ENV") != "dev" or os.environ.get("ODP_RELEASE_PROFILE") != "dev-admin":
            raise ProvisioningRefused("PROVISIONING_SCOPE_MISMATCH")
        svc = get_svc(request)
        with invitations._engine.lock:
            actor, tenant = invitations._actor(principal)
            if actor != PRESERVED_ACCOUNT_ID or tenant != TENANT_ID:
                raise ProvisioningRefused("PROVISIONING_SCOPE_MISMATCH")
            original = original_account_readback([svc.get_user(actor, tenant_id=tenant)])
            journal = ProvisioningJournal(engine=invitations._engine, audit_log=invitations._audit)
            binding = DevSmokeBindingJournal(journal=journal)
            if body is not None:
                action = body.get("action")
                if action == "reserve" and set(body) == {"action", "plan"}:
                    journal.reserve(body["plan"], original_account=original,
                                    release_sha=os.environ.get("ODAY_RELEASE_SHA", ""),
                                    manifest_digest=os.environ.get("ODP_RELEASE_MANIFEST_DIGEST", ""))
                elif action == "quarantine" and set(body) == {"action", "reservation"}:
                    receipt = body["reservation"]
                    if (not isinstance(receipt, dict) or set(receipt) != {
                            "execution_id", "plan_digest", "event_id", "stage", "authorization_id",
                            "execution_authorized", "secret_values_redacted"}
                            or receipt["authorization_id"] != AUTHORIZATION_ID
                            or receipt["execution_authorized"] is not False or receipt["secret_values_redacted"] is not True):
                        raise ProvisioningRefused("PROVISIONING_RESERVATION_MISMATCH")
                    journal.require_recovery(JournalReservation(*(receipt[key] for key in (
                        "execution_id", "plan_digest", "event_id", "stage"))))
                elif action in {"binding-intent", "binding-acknowledged", "recovery-required"} and set(body) == {
                    "action", "account_id", "execution_id", "plan_digest",
                }:
                    reserved = journal.inspect()
                    if (reserved is None or reserved.stage != "reserved"
                            or body["execution_id"] != reserved.execution_id
                            or body["plan_digest"] != reserved.plan_digest):
                        raise ProvisioningRefused("PROVISIONING_RESERVATION_MISMATCH")
                    existing = binding.inspect()
                    if action == "binding-intent":
                        if existing is not None:
                            raise ProvisioningRefused("PROVISIONING_BINDING_ALREADY_ATTEMPTED")
                        account = svc.get_user(body["account_id"], tenant_id=tenant)
                        provenance = invitation_provenance(account, svc.get_audit_trail(tenant_id=tenant))
                        root = journal._events()[0]
                        if (provenance is None or provenance["issuer_account_id"] != actor
                                or account.get("username") != "odp-dev-smoke"
                                or not any(e.event_id == provenance["issue_event_id"]
                                    and datetime.fromisoformat(str(e.occurred_at)) >= datetime.fromisoformat(str(root.occurred_at))
                                    for e in invitations._audit.list_events(tenant_id=tenant))):
                            raise ProvisioningRefused("PROVISIONING_PROVENANCE_INVALID")
                        metadata = {
                            **root.metadata, "account_id": body["account_id"],
                            "secret_name": CREDENTIAL_BUNDLE_SECRET_NAME, "stage": "binding-intent",
                            "credential_binding_verified": False,
                        }
                    else:
                        if existing is None or body["account_id"] != existing["account_id"]:
                            raise ProvisioningRefused("PROVISIONING_RESERVATION_MISMATCH")
                        metadata = {k: v for k, v in existing.items() if k != "audit_event_id"}
                    binding._append(metadata, action)
                else:
                    raise ProvisioningRefused("PROVISIONING_PLAN_INVALID")
            reservation = journal.inspect()
            return {"reservation": reservation.to_receipt() if reservation else None,
                    "binding": binding.inspect(), "execution_authorized": False,
                    "secret_values_redacted": True}

    @router.get("/dev-smoke-journal", dependencies=manage_deps, operation_id="inspectDevSmokeJournal")
    def inspect_dev_smoke_journal(request: Request) -> JSONResponse:
        try:
            return JSONResponse(journal_operation(request, None), headers={"cache-control": "no-store"})
        except (InvitationRefused, HTTPException):
            return JSONResponse({"error": {"code": "PROVISIONING_JOURNAL_REFUSED"}}, status_code=403,
                                headers={"cache-control": "no-store"})
        except Exception:
            return JSONResponse({"error": {"code": "PROVISIONING_JOURNAL_UNAVAILABLE"}}, status_code=503,
                                headers={"cache-control": "no-store"})

    @router.post("/dev-smoke-journal", dependencies=manage_deps, operation_id="writeDevSmokeJournal",
                 openapi_extra={"requestBody": {"required": True, "content": {
                     "application/json": {"schema": {"type": "object", "additionalProperties": False,
                         "properties": {"action": {"type": "string"}, "plan": {"type": "object"},
                             "reservation": {"type": "object"}, "account_id": {"type": "string"},
                             "execution_id": {"type": "string"}, "plan_digest": {"type": "string"}},
                         "required": ["action"]}}
                 }}})
    async def write_dev_smoke_journal(request: Request) -> JSONResponse:
        from shared.identity.dev_smoke_journal import ProvisioningRefused
        try:
            parsed = await _invitation_body(request, limit=8192)
            result = await run_in_threadpool(journal_operation, request, parsed)
            return JSONResponse(result, headers={"cache-control": "no-store"})
        except ProvisioningRefused as exc:
            return JSONResponse({"error": {"code": exc.code}}, status_code=409,
                                headers={"cache-control": "no-store"})
        except (InvitationRefused, HTTPException):
            return JSONResponse({"error": {"code": "PROVISIONING_JOURNAL_REFUSED"}}, status_code=403,
                                headers={"cache-control": "no-store"})
        except Exception:
            return JSONResponse({"error": {"code": "PROVISIONING_JOURNAL_UNAVAILABLE"}}, status_code=503,
                                headers={"cache-control": "no-store"})

    @router.get("/{subject_id}", dependencies=read_deps)
    def get_user(subject_id: str, request: Request) -> dict[str, Any]:
        svc = get_svc(request)
        try:
            return svc.get_user(subject_id, **tenant_kwargs(svc, request))
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except UserNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @router.post("", dependencies=manage_deps)
    def save_user(
        body: UserSavePayload,
        request: Request,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        scope_dict = (
            body.scope.model_dump(exclude_unset=True)
            if body.scope is not None
            else None
        )
        server_actor = getattr(request.state, "operator_subject_id", None) or "operator"
        server_role = getattr(request.state, "operator_role_id", None) or "platform_admin"
        partition_tenant = caller_tenant(request)
        try:
            user = svc.save_user(
                **actor_kwargs(svc, request),
                subject_id=body.subjectId,
                roles=body.roles,
                scope=scope_dict,
                attributes=body.attributes,
                email=body.email,
                name=body.name,
                status=body.status,
                actor_name=server_actor,
                actor_role=server_role,
                reason=body.reason,
                correlation_id=getattr(request.state, "correlation_id", None),
                tenant_id=partition_tenant,
            )
            return {
                "user": user,
                "message": f"User '{body.subjectId}' role and scope saved successfully.",
                "correlation_id": getattr(request.state, "correlation_id", None),
            }
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post("/{subject_id}/status", dependencies=manage_deps)
    def set_status(
        subject_id: str,
        body: UserStatusPayload,
        request: Request,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        server_actor = getattr(request.state, "operator_subject_id", None) or "operator"
        partition_tenant = caller_tenant(request)
        try:
            user = svc.set_user_status(
                **actor_kwargs(svc, request),
                subject_id=subject_id,
                status=body.status,
                actor_name=server_actor,
                reason=body.reason,
                correlation_id=getattr(request.state, "correlation_id", None),
                tenant_id=partition_tenant,
            )
            return {
                "user": user,
                "message": f"User '{subject_id}' status updated to {body.status}.",
                "correlation_id": getattr(request.state, "correlation_id", None),
            }
        except UserNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post("/invite", dependencies=manage_deps)
    @router.post("/create", dependencies=manage_deps)
    def invite_user(
        body: UserInvitePayload,
        request: Request,
    ) -> dict[str, Any]:
        svc = get_svc(request)
        scope_dict = (
            body.scope.model_dump(exclude_unset=True)
            if body.scope is not None
            else None
        )
        server_actor = getattr(request.state, "operator_subject_id", None) or "operator"
        server_role = getattr(request.state, "operator_role_id", None) or "platform_admin"
        partition_tenant = caller_tenant(request)
        initial_pwd = body.initialPassword or body.initial_password
        display_name = body.displayName or body.name
        try:
            result = svc.create_user(
                **actor_kwargs(svc, request),
                username=body.username,
                email=body.email,
                display_name=display_name,
                roles=body.roles,
                scope=scope_dict,
                initial_password=initial_pwd,
                actor_name=server_actor,
                actor_role=server_role,
                reason=body.reason,
                correlation_id=getattr(request.state, "correlation_id", None),
                tenant_id=partition_tenant,
            )
            return {
                "user": result["user"],
                "temporary_password": result["temporary_password"],
                "initial_password": result["initial_password"],
                "invitation_token": result.get("invitation_token"),
                "must_change": result.get("must_change", True),
                "message": f"User '{body.username}' invited successfully.",
                "correlation_id": getattr(request.state, "correlation_id", None),
            }
        except UserRolePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    return router


__all__ = ["create_user_role_sub_router"]
