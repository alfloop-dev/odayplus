"""Trusted, durable native field-assignment routes; no media claim or fallback."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from apps.api.oday_api.security.dependencies import (
    OPERATOR_NETWORK_READ_RESOURCE,
    build_engine,
    require_operator_permission,
)
from modules.market_survey.application.assignment_content import SurveyContentConflict
from modules.market_survey.application.assignment_owner import (
    SurveyAssignmentForbidden,
    SurveyAssignmentNotFound,
)
from modules.market_survey.domain.models import (
    SurveyAuthorizationError,
    SurveyStateConflictError,
    SurveyValidationError,
)
from modules.market_survey.infrastructure.assignment_repository import (
    SurveyOwnerUnavailable,
    SurveyRevisionConflict,
)
from shared.api.idempotency import IdempotencyConflictError
from shared.auth import Action
from shared.infrastructure.persistence.command_receipts import (
    CommandReceiptIncompleteError,
    CommandReceiptPersistenceError,
)


class CreateFieldAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targetKind: Literal["CANDIDATE_SITE", "HEAT_ZONE"]
    targetId: StrictStr = Field(min_length=1)
    assignedTo: StrictStr = Field(min_length=1)
    expiresAt: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)


class ChangeFieldAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: StrictInt = Field(ge=1)
    reason: StrictStr = Field(min_length=1)
    assignedTo: StrictStr | None = None
    expiresAt: StrictStr | None = None


class SurveyLocationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: StrictFloat | StrictInt
    longitude: StrictFloat | StrictInt
    address: StrictStr | None = None
    h3_index: StrictStr | None = None
    srid: StrictInt = 4326


class UploadFieldAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: StrictInt = Field(ge=1)
    dataBase64: StrictStr = Field(min_length=1)
    contentType: StrictStr = Field(min_length=1)
    mediaKind: Literal["PHOTO", "VIDEO", "AUDIO", "DOCUMENT", "SIGNATURE", "OTHER"]
    capturedAt: StrictStr = Field(min_length=1)
    caption: StrictStr | None = None
    reason: StrictStr = Field(min_length=1)


class SubmitFieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: StrictInt = Field(ge=1)
    location: SurveyLocationPayload
    surveyedAt: StrictStr = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    mediaIds: list[StrictStr] = Field(default_factory=list)
    confidence: StrictFloat | StrictInt | None = None
    reason: StrictStr = Field(min_length=1)


def create_canonical_survey_assignment_router(*, factory: Any, audit_log: Any,
                                            boundary: Any) -> APIRouter:
    selected_boundary = boundary

    def require_bound_identity() -> None:
        # An absent captured owner cannot borrow a later mutable default.
        if selected_boundary is None:
            raise HTTPException(503, detail="SURVEY_IDENTITY_BOUNDARY_UNAVAILABLE")

    engine = build_engine(audit_log=audit_log)
    read_guard = require_operator_permission("listing", Action.VIEW, engine=engine,
        boundary=selected_boundary, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE)
    create_guard = require_operator_permission("listing", Action.CREATE, engine=engine,
        boundary=selected_boundary)
    update_guard = require_operator_permission("listing", Action.UPDATE, engine=engine,
        boundary=selected_boundary)
    router = APIRouter(prefix="/market-survey", tags=["market-survey"],
                       dependencies=[Depends(require_bound_identity)])

    def call(request: Request, method: str, **kwargs: Any):
        try:
            principal = request.state.operator_principal
            owner = factory(principal, correlation_id=request.state.correlation_id)
            if method in {"command", "upload_attachment", "submit_evidence"}:
                kwargs["correlation_id"] = request.state.correlation_id
            return getattr(owner, method)(principal, **kwargs)
        except (SurveyAssignmentForbidden, SurveyAuthorizationError) as exc:
            raise HTTPException(403, detail="SURVEY_ASSIGNMENT_PERMISSION_DENIED") from exc
        except SurveyAssignmentNotFound as exc:
            raise HTTPException(404, detail="SURVEY_ASSIGNMENT_NOT_FOUND") from exc
        except (SurveyRevisionConflict, SurveyStateConflictError, IdempotencyConflictError, SurveyContentConflict) as exc:
            raise HTTPException(409, detail="SURVEY_ASSIGNMENT_CONFLICT") from exc
        except (SurveyValidationError, ValueError) as exc:
            raise HTTPException(422, detail="SURVEY_ASSIGNMENT_INPUT_INVALID") from exc
        except (SurveyOwnerUnavailable, CommandReceiptIncompleteError,
                CommandReceiptPersistenceError) as exc:
            raise HTTPException(503, detail="SURVEY_ASSIGNMENT_OWNER_UNAVAILABLE") from exc
        except Exception as exc:
            # No repository failure is disguised as an empty task collection
            # or a successful assignment. Do not return raw storage details.
            raise HTTPException(503, detail="SURVEY_ASSIGNMENT_OWNER_UNAVAILABLE") from exc

    @router.get("/assignments", dependencies=[Depends(read_guard)])
    def list_assignments(request: Request, targetKind: Literal["CANDIDATE_SITE", "HEAT_ZONE"],
                         targetId: str):
        return call(request, "list", target_kind=targetKind, target_id=targetId)

    @router.get("/assignees", dependencies=[Depends(create_guard)])
    def list_assignees(request: Request, targetKind: Literal["CANDIDATE_SITE", "HEAT_ZONE"],
                       targetId: str):
        return call(request, "assignees", target_kind=targetKind, target_id=targetId)

    @router.get("/assignments/{assignment_id}", dependencies=[Depends(read_guard)])
    def get_assignment(assignment_id: str, request: Request):
        return call(request, "get", identity=assignment_id)

    @router.post("/assignments", status_code=201, dependencies=[Depends(create_guard)])
    def create_assignment(request: Request, body: CreateFieldAssignment,
                          key: str = Header(min_length=1, alias="Idempotency-Key")):
        return call(request, "command", operation="create", payload=body.model_dump(), idempotency_key=key)

    @router.get("/assignments/{assignment_id}/attachments", dependencies=[Depends(read_guard)])
    def read_attachments(assignment_id: str, request: Request):
        return call(request, "attachments", identity=assignment_id)

    @router.post("/assignments/{assignment_id}/attachments", status_code=201, dependencies=[Depends(update_guard)])
    def upload_attachment(assignment_id: str, request: Request, body: UploadFieldAttachment,
                          key: str = Header(min_length=1, alias="Idempotency-Key")):
        return call(request, "upload_attachment", identity=assignment_id,
                    payload=body.model_dump(), idempotency_key=key)

    @router.get("/assignments/{assignment_id}/attachments/{media_id}/content", dependencies=[Depends(read_guard)])
    def download_attachment(assignment_id: str, media_id: str, request: Request):
        value = call(request, "download_attachment", identity=assignment_id, media_id=media_id)
        return Response(content=value["data"], media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="survey-{value["mediaId"]}.bin"',
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": value["sha256"], "X-Audit-Event-Id": value["auditEventId"]})

    @router.post("/assignments/{assignment_id}/submit", dependencies=[Depends(update_guard)])
    def submit_evidence(assignment_id: str, request: Request, body: SubmitFieldEvidence,
                        key: str = Header(min_length=1, alias="Idempotency-Key")):
        return call(request, "submit_evidence", identity=assignment_id,
                    payload=body.model_dump(), idempotency_key=key)

    @router.get("/assignments/{assignment_id}/evidence", dependencies=[Depends(read_guard)])
    def read_evidence(assignment_id: str, request: Request):
        return call(request, "evidence", identity=assignment_id)

    @router.post("/assignments/{assignment_id}/{operation}", dependencies=[Depends(update_guard)])
    def change_assignment(assignment_id: str, operation: Literal["assign", "claim", "start", "cancel"],
                          request: Request, body: ChangeFieldAssignment,
                          key: str = Header(min_length=1, alias="Idempotency-Key")):
        return call(request, "command", operation=operation, assignment_id=assignment_id,
                    payload=body.model_dump(exclude_none=True), idempotency_key=key)

    return router
