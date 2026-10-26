"""Preliminary area planning intent before native NetPlan admission."""
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from modules.netplan.domain.area_planning_draft import (
    AreaPlanningDraftConflict,
    AreaPlanningDraftInvalid,
)
from modules.netplan.infrastructure.area_planning_drafts import AreaPlanningDraftPersistenceError
from modules.opsboard.application.area_planning_draft_owner import AreaPlanningDraftOwner
from shared.api.idempotency import IdempotencyConflictError
from shared.infrastructure.persistence.command_receipts import (
    CommandReceiptIncompleteError,
    CommandReceiptPersistenceError,
)


class AreaPlanningDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    heatZoneId: str = Field(min_length=1, max_length=200)
    quarter: str | None = Field(default=None, pattern=r"^[1-9][0-9]{3}Q[1-4]$")
    budget: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    candidateIds: list[str] = Field(default_factory=list)
    expectedVersion: int | None = Field(default=None, ge=1)


class AreaPlanningDraftResponse(BaseModel):
    draftId: UUID
    tenantId: UUID
    createdBy: UUID
    heatZoneId: str
    name: str
    quarter: str | None
    budget: float | None
    candidateIds: list[str]
    version: int = Field(ge=1)
    status: Literal["DRAFT", "MATERIALIZED"]
    createdAt: datetime
    updatedAt: datetime
    nativeScenarioId: str | None
    materializedBy: UUID | None


class AreaPlanningDraftListResponse(BaseModel):
    heatZoneId: str
    items: list[AreaPlanningDraftResponse]


class AreaPlanningDraftCommandResponse(BaseModel):
    draft: AreaPlanningDraftResponse
    auditEventIds: list[UUID] = Field(min_length=1)
    receiptId: str
    replayed: bool


def create_area_planning_draft_sub_router(
    *, require_view: Callable[..., Any], require_create: Callable[..., Any],
    require_update: Callable[..., Any], owner_for_request: Callable[[Request], AreaPlanningDraftOwner],
) -> APIRouter:
    router = APIRouter(prefix="/area-planning-drafts")

    def call(request: Request, operation: Callable[[AreaPlanningDraftOwner], Any]):
        try:
            return operation(owner_for_request(request))
        except AreaPlanningDraftInvalid as exc:
            raise HTTPException(422, str(exc)) from None
        except (AreaPlanningDraftConflict, IdempotencyConflictError):
            raise HTTPException(409, "AREA_PLANNING_COMMAND_CONFLICT") from None
        except CommandReceiptIncompleteError:
            raise HTTPException(409, "AREA_PLANNING_COMMAND_INCOMPLETE") from None
        except (AreaPlanningDraftPersistenceError, CommandReceiptPersistenceError):
            raise HTTPException(503, "AREA_PLANNING_DURABLE_STATE_UNAVAILABLE") from None

    @router.get(
        "", response_model=AreaPlanningDraftListResponse, dependencies=[Depends(require_view)],
        operation_id="listOperatorAreaPlanningDrafts",
    )
    def list_drafts(request: Request, heatZoneId: str = Query(min_length=1, max_length=200)):
        return call(request, lambda owner: owner.list(heatZoneId))

    @router.get(
        "/{identity}", response_model=AreaPlanningDraftResponse,
        dependencies=[Depends(require_view)], operation_id="getOperatorAreaPlanningDraft",
    )
    def get_draft(identity: UUID, request: Request):
        return call(request, lambda owner: owner.get(str(identity)))

    @router.post(
        "", status_code=201, response_model=AreaPlanningDraftCommandResponse,
        dependencies=[Depends(require_create)], operation_id="createOperatorAreaPlanningDraft",
    )
    def create_draft(
        body: AreaPlanningDraftInput, request: Request,
        key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128,
                          pattern=r"^[A-Za-z0-9._:-]+$"),
    ):
        if body.expectedVersion is not None:
            raise HTTPException(422, "AREA_PLANNING_CREATE_VERSION_SERVER_OWNED")
        return call(request, lambda owner: owner.command(
            operation="create", payload=body.model_dump(exclude={"expectedVersion"}), key=key,
        ))

    @router.put(
        "/{identity}", response_model=AreaPlanningDraftCommandResponse,
        dependencies=[Depends(require_update)], operation_id="updateOperatorAreaPlanningDraft",
    )
    def update_draft(
        identity: UUID, body: AreaPlanningDraftInput, request: Request,
        key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128,
                          pattern=r"^[A-Za-z0-9._:-]+$"),
    ):
        if body.expectedVersion is None:
            raise HTTPException(428, "AREA_PLANNING_EXPECTED_VERSION_REQUIRED")
        return call(request, lambda owner: owner.command(
            operation="update", payload=body.model_dump(), key=key, identity=str(identity),
        ))

    return router
