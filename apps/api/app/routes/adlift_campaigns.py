"""Original campaign configuration API; this stage grants no spend authority."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, StrictStr, model_validator

from apps.api.oday_api.security.dependencies import (
    build_engine,
    default_boundary,
    require_permission,
)
from modules.adlift.application.campaign import AdLiftCampaignService
from modules.adlift.domain.campaign import (
    CampaignConflict,
    CampaignForbidden,
    CampaignInvalid,
    CampaignNotFound,
    CampaignUnavailable,
)
from shared.api.idempotency import IdempotencyConflictError
from shared.auth import Action
from shared.infrastructure.persistence.command_receipts import (
    CommandReceiptIncompleteError,
    CommandReceiptPersistenceError,
)


class CampaignBudgetProposalPayload(BaseModel):
    budget_amount: StrictStr
    budget_currency: StrictStr
    declared_data_version: StrictStr
    reason: StrictStr

    @model_validator(mode="before")
    @classmethod
    def safe_shape(cls, value: Any) -> Any:
        # Reject the complete raw shape with constant detail before Pydantic
        # can echo nonfinite JSON values through the shared error renderer.
        fields = set(cls.model_fields)
        if (not isinstance(value, dict) or set(value) != fields
                or any(not isinstance(v, str) for v in value.values())):
            raise HTTPException(422, detail="CAMPAIGN_INPUT_INVALID")
        return value


class CampaignCreatePayload(BaseModel):
    budget_amount: StrictStr
    budget_currency: StrictStr
    declared_data_version: StrictStr
    reason: StrictStr
    name: StrictStr
    channel: StrictStr
    treatment_store_ids: list[StrictStr]
    period_start: StrictStr
    period_end: StrictStr
    creative_reference: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def safe_shape(cls, value: Any) -> Any:
        fields = set(cls.model_fields)
        required = fields - {"creative_reference"}
        if (not isinstance(value, dict) or not required.issubset(value)
                or not set(value).issubset(fields)):
            raise HTTPException(422, detail="CAMPAIGN_INPUT_INVALID")
        if any(not isinstance(v, str) for k, v in value.items()
               if k != "treatment_store_ids" and not (k == "creative_reference" and v is None)):
            raise HTTPException(422, detail="CAMPAIGN_INPUT_INVALID")
        stores = value.get("treatment_store_ids")
        if not isinstance(stores, list) or not stores or any(not isinstance(v, str) for v in stores):
            raise HTTPException(422, detail="CAMPAIGN_INPUT_INVALID")
        return value


def create_adlift_campaign_router(*, repository: Any, audit_log: Any, job_queue: Any,
                                  store_repository: Any, brand_repository: Any,
                                  boundary: Any = None, campaign_budget_authority: Any = None) -> APIRouter:
    router = APIRouter(prefix="/campaigns", tags=["adlift"])
    owner = campaign_budget_authority.campaigns if campaign_budget_authority is not None else AdLiftCampaignService(
        repository=repository, audit_log=audit_log, job_queue=job_queue,
        store_repository=store_repository, brand_repository=brand_repository)
    selected_boundary = boundary if boundary is not None else default_boundary()
    engine = build_engine(audit_log=audit_log)

    def permission(action: Action):
        guard = require_permission("adlift", action, engine=engine, boundary=selected_boundary)

        def dependency(request: Request):
            # New campaign commands never accept spoofable header identities,
            # even if business-data readiness is disabled for a deployment.
            if selected_boundary is None:
                raise HTTPException(401, detail="CAMPAIGN_AUTH_BOUNDARY_REQUIRED")
            return guard(request)

        return dependency

    create_dependency = Depends(permission(Action.CREATE))
    view_dependency = Depends(permission(Action.VIEW))

    def invoke(operation):
        try:
            return operation()
        except CampaignForbidden as exc:
            raise HTTPException(403, detail=str(exc)) from exc
        except CampaignNotFound as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except (CampaignConflict, IdempotencyConflictError, CommandReceiptIncompleteError) as exc:
            raise HTTPException(409, detail="CAMPAIGN_COMMAND_CONFLICT") from exc
        except CampaignInvalid as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        except (CampaignUnavailable, CommandReceiptPersistenceError) as exc:
            raise HTTPException(503, detail="CAMPAIGN_OWNER_UNAVAILABLE") from exc
        except HTTPException:
            raise
        except Exception as exc:
            # Owner failures must not expose SQL, credentials, or partial writes.
            raise HTTPException(503, detail="CAMPAIGN_COMMAND_UNAVAILABLE") from exc

    @router.post("", status_code=201)
    def create_campaign(body: CampaignCreatePayload, request: Request,
                        principal=create_dependency,
                        idempotency_key: str = Header(alias="Idempotency-Key")):
        return invoke(lambda: owner.create(principal=principal, payload=body.model_dump(),
            idempotency_key=idempotency_key, correlation_id=request.state.correlation_id))

    @router.post("/{campaign_id}/budget-revisions")
    def propose_budget(campaign_id: str, body: CampaignBudgetProposalPayload, request: Request,
                       principal=create_dependency,
                       idempotency_key: str = Header(alias="Idempotency-Key"),
                       if_match: str = Header(alias="If-Match")):
        if len(if_match) > 20 or re.fullmatch(r'[1-9][0-9]*', if_match) is None:
            raise HTTPException(422, detail="CAMPAIGN_EXPECTED_REVISION_REQUIRED")
        return invoke(lambda: owner.propose_budget(campaign_id, principal=principal,
            payload=body.model_dump(), expected_revision=int(if_match),
            idempotency_key=idempotency_key, correlation_id=request.state.correlation_id))

    @router.get("/{campaign_id}")
    def get_campaign(campaign_id: str, request: Request,
                     principal=view_dependency):
        return invoke(lambda: owner.get(campaign_id, principal=principal,
                                       correlation_id=request.state.correlation_id))

    return router
