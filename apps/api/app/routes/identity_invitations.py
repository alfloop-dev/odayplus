"""Capability acceptance adapter, not an authentication or registration route.

Mounted with the paired bounded Web adapter and durable acceptance budget. No
account can be created without an existing admin-issued single-use capability.
Cloud Run's existing Web-to-API transport IAM remains required at deployment;
no user principal or browser identity headers authorize capability consumption.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from apps.api.app.routes.operator_modules.users_roles import _invitation_body, _invitation_error
from shared.identity.invitation_service import InvitationRefused, InvitationService


class InvitationAcceptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    invitation_id: str = Field(min_length=1, max_length=36)
    token: str = Field(min_length=1, max_length=43, repr=False, json_schema_extra={"writeOnly": True})
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024, repr=False, json_schema_extra={"writeOnly": True})
    display_name: str = Field(default="", max_length=255)


class InvitationAcceptedReceipt(BaseModel):
    status: Literal["accepted"]
    invitation_id: str
    account_id: str
    tenant_id: str
    audit_event_id: str


class InvitationCapabilityError(BaseModel):
    error: dict[str, str]


def create_invitation_acceptance_router(service: InvitationService | None) -> APIRouter:
    router = APIRouter(prefix="/auth/invitations", tags=["identity-invitations"])

    @router.post(
        "/accept", status_code=201, response_model=InvitationAcceptedReceipt,
        operation_id="acceptIdentityInvitation",
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": InvitationAcceptPayload.model_json_schema()}
        }}},
        responses={code: {"model": InvitationCapabilityError} for code in
                   (409, 413, 415, 422, 429, 503)},
    )
    async def accept_invitation(request: Request) -> JSONResponse:
        try:
            if service is None:
                raise HTTPException(503, detail={"code": "IDENTITY_PERSISTENCE_UNAVAILABLE"})
            parsed = await _invitation_body(request, limit=8192)
            try:
                payload = InvitationAcceptPayload.model_validate(parsed)
            except ValidationError:
                # Never pass validation errors to FastAPI: they contain the
                # rejected password/token/extra actor fields verbatim.
                raise HTTPException(422, detail={"code": "INVITATION_INPUT_INVALID"}) from None
            result = await run_in_threadpool(service.accept, **payload.model_dump())
            return JSONResponse(result.to_receipt(), status_code=201,
                                headers={"cache-control": "no-store"})
        except InvitationRefused as exc:
            return _invitation_error(exc)
        except HTTPException as exc:
            code = exc.detail["code"] if isinstance(exc.detail, dict) else "INVITATION_INPUT_INVALID"
            return JSONResponse({"error": {"code": code}}, status_code=exc.status_code,
                                headers={"cache-control": "no-store"})
        except Exception:
            return JSONResponse({"error": {"code": "IDENTITY_PERSISTENCE_UNAVAILABLE"}},
                                status_code=503, headers={"cache-control": "no-store"})

    return router
