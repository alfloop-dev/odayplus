"""Personal listing criteria, current authorized matches, and durable commands."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from modules.listing.application.saved_search import ListingCriteriaInvalid
from modules.listing.application.saved_search_owner import SavedListingSearchOwner
from shared.api.idempotency import IdempotencyConflictError
from shared.infrastructure.persistence.assisted_listing_intake import (
    AssistedIntakePersistenceConflict,
)
from shared.infrastructure.persistence.listing_search_matches import ListingSearchMatchConflict


class ListingSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    query: dict[str, Any]
    expectedVersion: int | None = Field(default=None, ge=1)


class ListingSearchDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expectedVersion: int = Field(ge=1)


class ListingQueryResponse(BaseModel):
    kind: Literal["listing-search.v1"]
    heatZoneId: str
    minAreaPing: float | None
    maxAreaPing: float | None
    maxRentPerMonth: float | None
    groundFloorOnly: bool


class PersonalListingSearchResponse(BaseModel):
    saved_view_id: UUID
    owner_subject_id: UUID
    tenant_id: UUID
    visibility: Literal["PRIVATE"]
    name: str
    query: ListingQueryResponse
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime | None
    deleted_at: datetime | None
    resource: Literal["intake"]
    shared_role: None


class ListingSearchListResponse(BaseModel):
    heatZoneId: str
    items: list[PersonalListingSearchResponse]


class ListingSearchCommandResponse(BaseModel):
    search: PersonalListingSearchResponse
    auditEventIds: list[UUID] = Field(min_length=1)
    receiptId: str
    replayed: bool


class ListingSearchMatchResponse(BaseModel):
    intakeId: UUID
    intakeVersion: int = Field(ge=1)
    recordedAt: datetime


class ListingSearchResultsResponse(BaseModel):
    search: PersonalListingSearchResponse
    items: list[ListingSearchMatchResponse]
    total: int = Field(ge=0)


def create_listing_search_sub_router(require_view: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/searches", dependencies=[Depends(require_view)])

    def context(request: Request):
        # The enclosing operator permission dependency binds this principal;
        # caller-selected persona/header fields cannot attest ownership.
        principal = getattr(request.state, "operator_principal", None)
        if principal is None:
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")
        owner = SavedListingSearchOwner.from_runtime(request.app.state)
        return owner, principal

    @router.get(
        "",
        response_model=ListingSearchListResponse,
        operation_id="listOperatorPersonalListingSearches",
    )
    def list_searches(request: Request, heatZoneId: str = Query(min_length=1, max_length=200)):
        owner, principal = context(request)
        return owner.list(principal, heat_zone_id=heatZoneId)

    @router.get(
        "/{identity}",
        response_model=PersonalListingSearchResponse,
        operation_id="getOperatorPersonalListingSearch",
    )
    def get_search(identity: str, request: Request):
        owner, principal = context(request)
        return owner.get(principal, identity)

    @router.get(
        "/{identity}/results",
        response_model=ListingSearchResultsResponse,
        operation_id="getOperatorPersonalListingSearchResults",
    )
    def search_results(identity: str, request: Request):
        owner, principal = context(request)
        return owner.results(principal, identity)

    def command(request, operation, body, key, identity=None):
        owner, principal = context(request)
        try:
            return owner.command(
                principal,
                operation=operation,
                payload=body,
                key=key,
                correlation_id=request.state.correlation_id,
                identity=identity,
            )
        except ListingCriteriaInvalid:
            raise HTTPException(422, "LISTING_SEARCH_CRITERIA_INVALID") from None
        except (
            AssistedIntakePersistenceConflict,
            ListingSearchMatchConflict,
            IdempotencyConflictError,
        ):
            raise HTTPException(409, "LISTING_SEARCH_COMMAND_CONFLICT") from None

    @router.post(
        "",
        status_code=201,
        response_model=ListingSearchCommandResponse,
        operation_id="createOperatorPersonalListingSearch",
    )
    def create_search(
        body: ListingSearchInput,
        request: Request,
        key: str = Header(
            alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
        ),
    ):
        if body.expectedVersion is not None:
            raise HTTPException(422, "LISTING_SEARCH_CREATE_VERSION_SERVER_OWNED")
        return command(request, "create", body.model_dump(exclude_none=True), key)

    @router.put(
        "/{identity}",
        response_model=ListingSearchCommandResponse,
        operation_id="updateOperatorPersonalListingSearch",
    )
    def update_search(
        identity: str,
        body: ListingSearchInput,
        request: Request,
        key: str = Header(
            alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
        ),
    ):
        if body.expectedVersion is None:
            raise HTTPException(428, "LISTING_SEARCH_EXPECTED_VERSION_REQUIRED")
        return command(request, "update", body.model_dump(), key, identity)

    @router.delete(
        "/{identity}",
        response_model=ListingSearchCommandResponse,
        operation_id="deleteOperatorPersonalListingSearch",
    )
    def delete_search(
        identity: str,
        body: ListingSearchDeleteInput,
        request: Request,
        key: str = Header(
            alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
        ),
    ):
        return command(request, "delete", body.model_dump(), key, identity)

    return router
