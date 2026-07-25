"""Operator Console Network Review decision routes (ODP-OC-R4-007).

Owns:
- GET  /operator/network-reviews
- POST /operator/network-reviews/reset
- POST /operator/network-reviews/{review_id}/decide

The decide endpoint applies one review decision atomically across Candidate,
Review, Approval, Decision, and Audit records (``NetworkReviewService``). The
decision-authority rule is enforced at two layers:

- HTTP guard: the decide route requires ``sitescore`` + ``Action.APPROVE``,
  which is granted to Site Reviewer / Executive but **not** to Expansion — so
  an Expansion caller fails closed with 403 before the handler runs.
- Service guard: a defense-in-depth allowlist raises 403 if a mis-scoped actor
  role reaches the service.

Reason / condition / override policy violations surface as 422, an
already-decided review as 409, and an unknown review as 404. They compose
through apps.api.app.routes.operator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, field_validator

from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.opsboard.application.network_reviews import (
    DECISION_ACTIONS,
    NetworkReviewConflict,
    NetworkReviewNotFound,
    NetworkReviewPolicyError,
    NetworkReviewRoleError,
    NetworkReviewRuntimeUnavailable,
    NetworkReviewService,
)


class ReviewDecisionPayload(BaseModel):
    """POST /operator/network-reviews/{review_id}/decide."""

    model_config = ConfigDict(extra="allow")

    decision: str
    reason: str = ""
    conditions: str | None = None
    requiredData: list[str] = []
    overrideAck: bool = False
    actorRoleId: str = "siteReviewer"
    actorName: str | None = None

    @field_validator("decision")
    @classmethod
    def valid_decision(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in DECISION_ACTIONS:
            raise ValueError(f"decision must be one of {', '.join(DECISION_ACTIONS)}")
        return normalized


def create_network_review_sub_router(
    service: NetworkReviewService,
    *,
    require_view_permission_fn: Callable[..., Any],
    require_decide_permission_fn: Callable[..., Any],
    service_resolver: Callable[[Request], Any] | None = None,
    allow_reset: bool = True,
) -> APIRouter:
    router = APIRouter(prefix="/network-reviews")

    def require_reset_allowed() -> None:
        if not allow_reset:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PRODUCTION_RESET_DENIED",
                    "message": "network review reset is disabled in live mode",
                },
            )

    @router.get("", dependencies=[Depends(require_view_permission_fn)])
    @router.get("/", dependencies=[Depends(require_view_permission_fn)])
    def get_network_reviews(
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).snapshot(
                correlation_id=x_correlation_id
            )
        except NetworkReviewRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    @router.post(
        "/reset",
        dependencies=[
            Depends(require_decide_permission_fn),
            Depends(require_reset_allowed),
        ],
    )
    def reset_network_reviews(request: Request) -> dict[str, Any]:
        return resolve_service(request, service, service_resolver).reset()

    @router.post(
        "/{review_id}/decide",
        dependencies=[Depends(require_decide_permission_fn)],
    )
    def decide_review(
        review_id: str,
        body: ReviewDecisionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return resolve_service(request, service, service_resolver).decide_review(
                review_id=review_id,
                decision=body.decision,
                reason=body.reason,
                conditions=body.conditions,
                required_data=body.requiredData,
                override_ack=body.overrideAck,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
        except NetworkReviewNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkReviewRoleError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except NetworkReviewConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkReviewPolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        except NetworkReviewRuntimeUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=exc.to_detail(),
            ) from exc

    return router


__all__ = ["create_network_review_sub_router"]
