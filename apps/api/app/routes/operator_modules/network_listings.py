"""Operator Console Network Listing Radar routes.

Owns:
- GET /operator/network-listings
- POST /operator/network-listings/listings/{id}/convert
- POST /operator/network-listings/listings/{id}/merge
- POST /operator/network-listings/listings/{id}/archive

The routes wrap NetworkListingService and keep write auth/idempotency headers at
the HTTP boundary. They compose through apps.api.app.routes.operator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
    NetworkListingNotFound,
    NetworkListingPolicyError,
    NetworkListingService,
)


class NetworkListingActorPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    actorRoleId: str = "expansionManager"
    actorName: str | None = None
    reason: str | None = None


class NetworkListingMergePayload(NetworkListingActorPayload):
    targetListingId: str


def create_network_listings_sub_router(
    service: NetworkListingService,
    *,
    require_view_permission_fn: Callable[..., Any],
    require_write_permission_fn: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/network-listings")

    @router.get("", dependencies=[Depends(require_view_permission_fn)])
    @router.get("/", dependencies=[Depends(require_view_permission_fn)])
    def get_network_listings(
        selected_heat_zone_id: str | None = Query(default=None, alias="selectedHeatZoneId"),
        lens: str | None = None,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        return service.snapshot(
            selected_heat_zone_id=selected_heat_zone_id,
            lens=lens,
            correlation_id=x_correlation_id,
        )

    @router.post(
        "/reset",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def reset_network_listings() -> dict[str, Any]:
        return service.reset()

    @router.post(
        "/listings/{listing_id}/convert",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def convert_listing(
        listing_id: str,
        body: NetworkListingActorPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return service.convert_listing(
                listing_id=listing_id,
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/listings/{listing_id}/merge",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def merge_listing(
        listing_id: str,
        body: NetworkListingMergePayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return service.merge_listing(
                source_listing_id=listing_id,
                target_listing_id=body.targetListingId,
                reason=body.reason or "",
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/listings/{listing_id}/archive",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def archive_listing(
        listing_id: str,
        body: NetworkListingActorPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            return service.archive_listing(
                listing_id=listing_id,
                reason=body.reason or "",
                actor_role_id=body.actorRoleId,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


__all__ = ["create_network_listings_sub_router"]
