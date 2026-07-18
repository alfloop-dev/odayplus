"""Operator Console Privacy and Governance sub-router.

Exposes endpoints for purging, placing/releasing legal holds, evidence export,
manifest verification, and downloading evidence (ODP-INTAKE-PRIVACY-001).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from modules.listing.application.intake_privacy import IntakePrivacyService
from modules.listing.domain.intake_states import DenialCode, DomainValidationError


class PlaceHoldPayload(BaseModel):
    tenantId: str = Field(min_length=1)
    subjectType: str = Field(min_length=1)
    subjectId: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approvedBy: str = Field(min_length=1)


class ReleaseHoldPayload(BaseModel):
    tenantId: str = Field(min_length=1)
    subjectType: str = Field(min_length=1)
    subjectId: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approvedBy: str = Field(min_length=1)


class PurgePayload(BaseModel):
    tenantId: str = Field(min_length=1)
    subjectType: str = Field(min_length=1)
    subjectId: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approvedBy: str = Field(min_length=1)
    dryRun: bool = False


class ExportPayload(BaseModel):
    tenantId: str = Field(min_length=1)
    subjectType: str = Field(min_length=1)
    subjectId: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    authorizedBy: str = Field(min_length=1)
    authorizationId: str = Field(min_length=1)
    dataClassification: str = "restricted"
    sensitive: bool = True
    maskingProfile: str = "masked"
    destinationResidency: str = "TW_ONLY"


def create_privacy_sub_router(
    service: IntakePrivacyService,
    *,
    require_view_permission_fn: Any = None,
    require_write_permission_fn: Any = None,
) -> APIRouter:
    """Return the Privacy sub-router wired to IntakePrivacyService."""
    router = APIRouter(prefix="/privacy", tags=["operator-privacy"])

    read_deps = [Depends(require_view_permission_fn)] if require_view_permission_fn else []
    write_deps = [Depends(require_write_permission_fn)] if require_write_permission_fn else []

    def get_principal(request: Request) -> Any:
        principal = getattr(request.state, "operator_principal", None)
        if principal is None:
            from apps.api.oday_api.security.dependencies import principal_from_headers

            principal = principal_from_headers(request.headers)
        return principal

    def get_operator_role_id(request: Request) -> str | None:
        val = getattr(request.state, "operator_role_id", None)
        if val is None:
            val = request.headers.get("x-operator-role")
        return val

    def handle_domain_error(exc: DomainValidationError) -> None:
        code_to_status = {
            DenialCode.AUTHENTICATION_REQUIRED: status.HTTP_401_UNAUTHORIZED,
            DenialCode.ROLE_DENIED: status.HTTP_403_FORBIDDEN,
            DenialCode.TENANT_SCOPE_DENIED: status.HTTP_403_FORBIDDEN,
            DenialCode.SCOPE_DENIED: status.HTTP_403_FORBIDDEN,
            DenialCode.OWNERSHIP_REQUIRED: status.HTTP_403_FORBIDDEN,
            DenialCode.ASSIGNMENT_SCOPE_DENIED: status.HTTP_403_FORBIDDEN,
            DenialCode.SOURCE_SCOPE_DENIED: status.HTTP_403_FORBIDDEN,
            DenialCode.RESIDENCY_DENIED: status.HTTP_403_FORBIDDEN,
            DenialCode.LEGAL_HOLD_CONFLICT: status.HTTP_409_CONFLICT,
            DenialCode.WORKFLOW_STATE_DENIED: status.HTTP_409_CONFLICT,
            DenialCode.SECOND_ACTOR_REQUIRED: status.HTTP_409_CONFLICT,
            DenialCode.SELF_REVIEW_DENIED: status.HTTP_403_FORBIDDEN,
            DenialCode.EXPORT_APPROVAL_REQUIRED: status.HTTP_409_CONFLICT,
            DenialCode.RETENTION_NOT_REACHED: status.HTTP_409_CONFLICT,
            DenialCode.DEPENDENCY_CONFLICT: status.HTTP_409_CONFLICT,
            DenialCode.RISK_ACKNOWLEDGEMENT_REQUIRED: status.HTTP_422_UNPROCESSABLE_ENTITY,
        }
        status_code = code_to_status.get(exc.code, status.HTTP_400_BAD_REQUEST)
        raise HTTPException(status_code=status_code, detail=str(exc))

    @router.post("/hold", dependencies=write_deps)
    def place_hold(
        body: PlaceHoldPayload,
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        try:
            return service.place_legal_hold(
                principal=principal,
                tenant_id=body.tenantId,
                subject_type=body.subjectType,
                subject_id=body.subjectId,
                reason=body.reason,
                approved_by=body.approvedBy,
                operator_role_id=operator_role_id,
                correlation_id=x_correlation_id,
            )
        except DomainValidationError as exc:
            handle_domain_error(exc)

    @router.post("/hold/release", dependencies=write_deps)
    def release_hold(
        body: ReleaseHoldPayload,
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        try:
            return service.release_legal_hold(
                principal=principal,
                tenant_id=body.tenantId,
                subject_type=body.subjectType,
                subject_id=body.subjectId,
                reason=body.reason,
                approved_by=body.approvedBy,
                operator_role_id=operator_role_id,
                correlation_id=x_correlation_id,
            )
        except DomainValidationError as exc:
            handle_domain_error(exc)

    @router.post("/purge", dependencies=write_deps)
    def purge(
        body: PurgePayload,
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        try:
            return service.purge_subject(
                principal=principal,
                tenant_id=body.tenantId,
                subject_type=body.subjectType,
                subject_id=body.subjectId,
                reason=body.reason,
                approved_by=body.approvedBy,
                operator_role_id=operator_role_id,
                correlation_id=x_correlation_id,
                dry_run=body.dryRun,
            )
        except DomainValidationError as exc:
            handle_domain_error(exc)

    @router.post("/export", dependencies=write_deps)
    def export(
        body: ExportPayload,
        request: Request,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        try:
            return service.export_evidence(
                principal=principal,
                tenant_id=body.tenantId,
                subject_type=body.subjectType,
                subject_id=body.subjectId,
                purpose=body.purpose,
                authorized_by=body.authorizedBy,
                authorization_id=body.authorizationId,
                data_classification=body.dataClassification,
                sensitive=body.sensitive,
                masking_profile=body.maskingProfile,
                destination_residency=body.destinationResidency,
                operator_role_id=operator_role_id,
                correlation_id=x_correlation_id,
            )
        except DomainValidationError as exc:
            handle_domain_error(exc)

    @router.get("/export/verify/{export_id}", dependencies=read_deps)
    def verify(export_id: str) -> dict[str, Any]:
        return service.verify_export_manifest(export_id)

    @router.get("/export/download/{download_evidence_id}", dependencies=read_deps)
    def download(download_evidence_id: str) -> dict[str, Any]:
        return service.download_evidence(download_evidence_id)

    return router
