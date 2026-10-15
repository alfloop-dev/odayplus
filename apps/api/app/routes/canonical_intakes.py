"""Native Intake submission/read; processing remains an explicit next stage."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.app.routes.canonical_match_cases import _http_errors
from apps.api.oday_api.security.dependencies import principal_from_headers
from modules.listing.application.intake_authorization import authorize_intake_action
from modules.listing.domain.intake_states import IntakeStage
from shared.audit.backlog import CanonicalAuditBacklog


class NativeIntakeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_id: UUID | None = None
    region_id: UUID | None = None
    assigned_area_id: UUID | None = None
    heat_zone_id: UUID | None = None


class SubmitCanonicalIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_method: Literal["URL", "MANUAL"]
    original_url: str | None = Field(default=None, min_length=1, max_length=4096)
    scope: NativeIntakeScope
    reason: str = Field(min_length=3, max_length=4000)

    @field_validator("reason")
    @classmethod
    def explicit_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("An explicit submission reason is required")
        return value

    @model_validator(mode="after")
    def validate_submission(self):
        if self.intake_method == "MANUAL":
            if self.original_url is not None:
                raise ValueError("A manual Intake must not claim a URL submission")
        else:
            url = urlsplit(self.original_url or "")
            if (url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password
                    or any(char.isspace() for char in self.original_url or "")):
                raise ValueError("A URL Intake requires an HTTP(S) URL without credentials")
        return self


class CanonicalIntakeReceipt(BaseModel):
    intake_id: UUID
    processing_state: Literal["SUBMITTED"]
    version: int = Field(ge=1)
    intake_method: Literal["URL", "MANUAL"]
    submitted_at: datetime
    correlation_id: UUID
    transition_id: UUID
    audit_event_id: UUID
    outbox_event_id: UUID


class CanonicalIntakeSummary(BaseModel):
    intake_id: UUID
    tenant_id: UUID
    submitter_subject_id: UUID
    intake_method: Literal["URL", "MANUAL", "CSV", "APPROVED_FEED", "OPERATOR_SNAPSHOT"]
    processing_state: IntakeStage
    version: int = Field(ge=1)
    submitted_at: datetime
    correlation_id: UUID
    scope: NativeIntakeScope


def create_canonical_intakes_router(store) -> APIRouter:
    router = APIRouter(tags=["assisted-listing-intake"])

    def context(request):
        principal = principal_from_headers(request.headers)
        if not principal.authenticated:
            raise HTTPException(401, "principal not authenticated")
        try:
            UUID(principal.subject_id)
            UUID(principal.tenant_id)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(403, "CANONICAL_ACTOR_AND_TENANT_REQUIRED") from None
        bundle = getattr(request.app.state, "persistence_bundle", None)
        if (bundle is None or getattr(getattr(bundle, "engine", None), "dialect", None) != "postgresql"
                or getattr(bundle, "assisted_intake_store", None) is not store
                or getattr(store, "_engine", None) is not bundle.engine):
            raise HTTPException(503, "CANONICAL_INTAKE_STORAGE_UNAVAILABLE")
        factory = getattr(bundle, "canonical_intakes", None)
        if not callable(factory):
            raise HTTPException(503, "CANONICAL_INTAKE_STORAGE_UNAVAILABLE")
        try:
            return principal, factory()
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(503, "CANONICAL_INTAKE_STORAGE_UNAVAILABLE") from exc

    def authorize(request, principal, row, action="view"):
        authorize_intake_action(principal, action,
            resource={**row, "submitter": row.get("submitter_subject_id")},
            audit_log=getattr(request.app.state, "audit_log", None),
            correlation_id=request.headers.get("x-correlation-id"))

    errors = {code: {"description": description} for code, description in (
        (401, "Authentication required"), (403, "Current actor or resource scope denied"),
        (404, "Native Intake not found"), (409, "Concurrent command or idempotency conflict"),
        (422, "Invalid native submission"), (503, "Canonical storage or audit unavailable"),
    )}

    @router.get("/canonical-intakes/{intake_id}", operation_id="getCanonicalIntake",
                response_model=CanonicalIntakeSummary, responses=errors)
    def get(intake_id: UUID, request: Request, response: Response):
        with _http_errors():
            principal, repo = context(request)
            with repo.graph.transaction(principal.tenant_id):
                row = repo.get(principal.tenant_id, str(intake_id))
                authorize(request, principal, row)
                response.headers["ETag"] = f'W/"{row["version"]}"'
                return {**row, "scope": {key: row[key] for key in NativeIntakeScope.model_fields}}

    @router.post("/canonical-intakes", operation_id="submitCanonicalIntake", status_code=201,
                 response_model=CanonicalIntakeReceipt, responses=errors)
    def submit(body: SubmitCanonicalIntakeRequest, request: Request, response: Response,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
    ):
        with _http_errors():
            principal, repo = context(request)
            tenant, actor = principal.tenant_id, principal.subject_id
            scope = body.scope.model_dump(mode="json")
            digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            replay_key = f"{tenant}:{actor}:submitCanonicalIntake:{key}"
            with repo.graph.transaction(tenant):
                store.refresh(tenant)
                with store.transaction():
                    authorize(request, principal, {"scope": {"tenant_id": tenant, **scope}},
                              "submit_url" if body.intake_method == "URL" else "submit_manual")
                    prior = store.replays.get(replay_key)
                    if prior is not None:
                        if prior[0] != digest:
                            raise HTTPException(409, "IDEMPOTENCY_KEY_REUSED")
                        result = prior[1]
                        authorize(request, principal, repo.get(tenant, result["intake_id"]))
                        response.headers["Idempotency-Replayed"] = "true"
                    else:
                        CanonicalAuditBacklog(repo.engine).require_available(tenant)
                        result = repo.submit(tenant, actor, method=body.intake_method, original_url=body.original_url,
                            scope=scope, reason=body.reason, idempotency_key=key)
                        result = CanonicalIntakeReceipt.model_validate(result).model_dump(mode="json")
                        store.replays[replay_key] = (digest, result, 201)
                        store.flush()
                        response.headers["Idempotency-Replayed"] = "false"
                    response.headers["ETag"] = f'W/"{result["version"]}"'
                    return result

    return router
