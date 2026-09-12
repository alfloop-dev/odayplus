"""Operator comments API.

Comments are an attached audit record for a canonical task, decision, or
approval.  The router obtains tenant and actor identity from the verified
principal populated by the Operator permission dependency; neither may be
overridden by request JSON.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from apps.api.app.routes._common import resolve_tenant_id
from modules.opsboard.application.comments import (
    CommentForbidden,
    CommentNotFound,
    CommentPersistenceUnavailable,
    CommentPolicyError,
    CommentsService,
)

CommentTargetType = Literal["task", "decision", "approval"]


class CommentCreateRequest(BaseModel):
    targetType: CommentTargetType
    targetId: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=2_000)

    @field_validator("targetId", "content")
    @classmethod
    def trim_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value


class CommentEditRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)

    @field_validator("content")
    @classmethod
    def trim_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be empty")
        return value


def _actor_id(request: Request) -> str:
    principal = getattr(request.state, "operator_principal", None)
    actor = getattr(principal, "subject_id", None) or getattr(
        request.state, "operator_subject_id", None
    )
    if not actor or not str(actor).strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="verified actor identity is required for comments",
        )
    return str(actor).strip()


def _correlation_id(request: Request, header_value: str | None = None) -> str | None:
    return getattr(request.state, "correlation_id", None) or header_value


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, CommentNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, CommentForbidden):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, CommentPersistenceUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "OPERATOR_COMMENTS_PERSISTENCE_UNAVAILABLE",
                "message": str(exc),
            },
        )
    if isinstance(exc, CommentPolicyError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "OPERATOR_COMMENTS_UNAVAILABLE", "message": str(exc)},
    )


def create_comments_sub_router(
    service: CommentsService,
    *,
    require_view_permission_fn: Any = None,
    require_write_permission_fn: Any = None,
) -> APIRouter:
    """Build the tenant-scoped comments endpoints."""
    router = APIRouter(prefix="/comments", tags=["operator-comments"])
    read_deps = [Depends(require_view_permission_fn)] if require_view_permission_fn else []
    write_deps = [Depends(require_write_permission_fn)] if require_write_permission_fn else []

    @router.get("", dependencies=read_deps)
    def list_comments(
        request: Request,
        target_type: CommentTargetType = Query(alias="targetType"),  # noqa: B008
        target_id: str = Query(alias="targetId", min_length=1, max_length=255),  # noqa: B008
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            tenant_id = resolve_tenant_id(request)
            items = service.list_comments(
                tenant_id=tenant_id,
                target_type=target_type,
                target_id=target_id,
            )
        except Exception as exc:
            raise _translate(exc) from exc
        return {
            "items": items,
            "count": len(items),
            "targetType": target_type,
            "targetId": target_id.strip(),
            "correlationId": _correlation_id(request, x_correlation_id),
        }

    @router.post("", dependencies=write_deps)
    def create_comment(
        body: CommentCreateRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            result = service.create_comment(
                tenant_id=resolve_tenant_id(request),
                target_type=body.targetType,
                target_id=body.targetId,
                content=body.content,
                actor_id=_actor_id(request),
                correlation_id=_correlation_id(request, x_correlation_id),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            raise _translate(exc) from exc
        return {**result, "correlationId": _correlation_id(request, x_correlation_id)}

    @router.patch("/{comment_id}", dependencies=write_deps)
    def edit_comment(
        body: CommentEditRequest,
        request: Request,
        comment_id: str = Path(min_length=1, max_length=255),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            result = service.edit_comment(
                tenant_id=resolve_tenant_id(request),
                comment_id=comment_id,
                content=body.content,
                actor_id=_actor_id(request),
                correlation_id=_correlation_id(request, x_correlation_id),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            raise _translate(exc) from exc
        return {**result, "correlationId": _correlation_id(request, x_correlation_id)}

    return router


__all__ = ["CommentCreateRequest", "CommentEditRequest", "create_comments_sub_router"]
