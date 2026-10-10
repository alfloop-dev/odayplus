"""Capability acceptance adapter, not an authentication or registration route.

Factory is intentionally not mounted in runtime yet. The paired Web adapter,
origin policy and routing inventory must be complete before activation. No
account can be created without an existing admin-issued single-use capability.
Cloud Run's existing Web-to-API transport IAM remains required at deployment;
no user principal or browser identity headers authorize capability consumption.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from apps.api.app.routes.operator_modules.users_roles import _invitation_body, _invitation_error
from shared.identity.invitation_service import InvitationRefused, InvitationService


class InvitationAcceptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    invitation_id: str = Field(min_length=1, max_length=36)
    token: str = Field(min_length=1, max_length=43)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024, repr=False)
    display_name: str = Field(default="", max_length=255)


def create_invitation_acceptance_router(service: InvitationService | None) -> APIRouter:
    router = APIRouter(prefix="/auth/invitations", tags=["identity-invitations"])

    @router.post("/accept")
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
