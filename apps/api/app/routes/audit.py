"""Audit evidence export API."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from modules.opsboard.audit import AuditEvidenceExportError, AuditEvidenceExportService
from modules.opsboard.audit.application.evidence_artifact import (
    EvidenceArtifactUnavailable,
    render_evidence_archive,
    validate_retained_evidence_artifact,
)
from modules.opsboard.audit.application.evidence_export import decision_card_from_mapping
from modules.opsboard.audit.application.evidence_general_admission import (
    GeneralDecisionEvidenceSelection,
)
from modules.opsboard.audit.application.evidence_general_preview import (
    GeneralSiteScoreEvidencePreview,
)
from modules.opsboard.audit.application.evidence_general_read import GeneralSiteScoreRetainedReader
from modules.opsboard.audit.application.evidence_general_retention import (
    GeneralSiteScoreRetainedExport,
)
from modules.opsboard.audit.application.evidence_pdf import render_evidence_pdf
from modules.opsboard.audit.application.evidence_projection import EvidencePolicyScope
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from modules.opsboard.audit.domain.evidence import EvidenceExportRequest
from modules.opsboard.auth import AuthenticationBoundary
from shared.api.idempotency import IdempotencyConflictError
from shared.audit import AuditEvent, EvidenceGovernanceError, InMemoryAuditLog
from shared.audit.integrity import canonical_json
from shared.audit.persistence import (
    EvidenceBundleStore,
    EvidenceIntegrityError,
    GovernedEvidenceOperation,
    RetainedEvidence,
)
from shared.auth import Principal, Role

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from fastapi.responses import JSONResponse, Response
    from pydantic import BaseModel, ConfigDict, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    logger = logging.getLogger("oday-api.audit")

    class EvidenceExportPayload(BaseModel):
        program_id: str = Field(min_length=1)
        purpose: str = Field(min_length=1)
        requested_by: str = Field(min_length=1)
        from_time: str
        to_time: str
        correlation_ids: list[str] = Field(min_length=1)
        export_scope: str = Field(min_length=1)
        environment: str = "test"
        build_version: str = "local"
        data_classification: str = "internal"
        sensitive: bool = False
        decision_cards: list[dict[str, Any]] = Field(min_length=1)
        purpose_scope: str | None = None
        expires_at: str | None = None
        authorized_by: str | None = None
        authorization_id: str | None = None
        masking_profile: str = "masked"
        identity_boundary: str | None = None

    class CanonicalEvidenceExportPayload(BaseModel):
        """Selectors only; identity/classification/retention come from owners."""
        model_config = ConfigDict(extra="forbid")
        decision_id: str = Field(min_length=1)
        expected_decision_version: int = Field(strict=True, ge=1)
        sections: list[Literal["decision_log", "audit"]] = Field(min_length=1)
        reason: str = Field(min_length=1)
        from_time: str
        to_time: str
        format: Literal["json"] = "json"
        program_label: str | None = None
        expected_document_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    class EvidenceGovernancePayload(BaseModel):
        role: str = Field(min_length=1)
        reason: str = Field(min_length=1)
        correlation_id: str | None = None

    class EvidenceRetentionPurgePayload(EvidenceGovernancePayload):
        as_of: str

    _GOVERNANCE_ROLES_BY_PLATFORM_ROLE: dict[Role, frozenset[str]] = {
        Role.FINANCE_LEGAL: frozenset({"legal"}),
        Role.COMPLIANCE_OFFICER: frozenset({"compliance_officer"}),
        Role.RECORDS_MANAGER: frozenset({"records_manager"}),
        Role.RETENTION_MANAGER: frozenset({"retention_manager"}),
    }

    def create_audit_router(
        *,
        audit_log: InMemoryAuditLog | None = None,
        evidence_store: EvidenceBundleStore | None = None,
        service: AuditEvidenceExportService | None = None,
        boundary: AuthenticationBoundary | None = None,
        canonical_service_for_tenant: Callable[[str], GeneralSiteScoreRetainedExport] | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.runtime_mode import live_data_required
        from apps.api.oday_api.security.dependencies import (
            build_engine,
            default_boundary,
            require_permission,
        )
        from shared.auth import Action

        router = APIRouter(prefix="/audit", tags=["audit"])
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        selected_boundary = boundary if boundary is not None else default_boundary()

        def permission(action: Action):
            guard = require_permission(
                "audit", action, engine=authz_engine, boundary=selected_boundary
            )

            def dependency(request: Request) -> Principal:
                # A router created without an auth owner must never adopt a
                # later app's global owner via require_permission(None).
                if selected_boundary is None and default_boundary() is not None:
                    raise HTTPException(
                        status_code=401, detail="AUDIT_AUTH_BOUNDARY_CHANGED"
                    )
                return guard(request)

            return dependency

        export_guard = Depends(permission(Action.EXPORT))
        view_guard = Depends(permission(Action.VIEW))
        update_guard = Depends(permission(Action.UPDATE))
        delete_guard = Depends(permission(Action.DELETE))
        export_service = service or AuditEvidenceExportService(
            audit_log=active_audit_log, evidence_store=evidence_store
        )

        def canonical_service(principal: Principal) -> GeneralSiteScoreRetainedExport:
            if canonical_service_for_tenant is None:
                raise HTTPException(status_code=503, detail="CANONICAL_EVIDENCE_INPUT_REQUIRED")
            try:
                selected = canonical_service_for_tenant(principal.tenant_id)
                if not isinstance(selected, GeneralSiteScoreRetainedExport):
                    raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:general_owner")
                selected._binding(principal)
                return selected
            except SiteScoreEvidenceUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        def canonical_error(exc: Exception) -> HTTPException:
            if isinstance(exc, SiteScoreEvidenceDenied):
                return HTTPException(status_code=404, detail="retained evidence source not available")
            if isinstance(exc, IdempotencyConflictError):
                return HTTPException(status_code=409, detail="evidence command binding conflict")
            if isinstance(exc, SiteScoreEvidenceUnavailable):
                code = 410 if "purged_content" in str(exc) else 503
                return HTTPException(status_code=code, detail=str(exc))
            if isinstance(exc, ValueError):
                return HTTPException(status_code=422, detail=str(exc))
            return HTTPException(status_code=503, detail="CANONICAL_EVIDENCE_INPUT_REQUIRED")

        def canonical_bytes(export_id: str, request: Request, principal: Principal) -> bytes:
            try:
                return GeneralSiteScoreRetainedReader(canonical_service(principal)).content(
                    principal, export_id, correlation_id=request.state.correlation_id)
            except HTTPException:
                raise
            except Exception as exc:
                raise canonical_error(exc) from exc

        @router.get("/evidence/sources")
        def canonical_evidence_sources(principal=export_guard) -> dict[str, Any]:
            try:
                return {"sources": GeneralSiteScoreEvidencePreview(canonical_service(principal)).sources(principal),
                    "supported_modules": ["SiteScore"], "supported_sections": ["decision_log", "audit"],
                    "limits": "recorded_decision_audit_with_optional_partial_chain"}
            except HTTPException:
                raise
            except Exception as exc:
                raise canonical_error(exc) from exc

        @router.post("/evidence/preview")
        def preview_canonical_evidence(body: CanonicalEvidenceExportPayload, principal=export_guard) -> dict[str, Any]:
            try:
                selection = GeneralDecisionEvidenceSelection(
                    SiteScoreEvidenceSelector(body.decision_id, body.expected_decision_version, tuple(body.sections)),
                    body.reason, EvidencePolicyScope(body.format, "authenticated-response",
                        _parse_time(body.from_time), _parse_time(body.to_time)), body.program_label)
                return GeneralSiteScoreEvidencePreview(canonical_service(principal)).preview(principal, selection)
            except HTTPException:
                raise
            except Exception as exc:
                raise canonical_error(exc) from exc

        @router.post(
            "/evidence/export",
            status_code=status.HTTP_201_CREATED,
            responses={503: {"description": "Canonical production evidence inputs not admitted"}},
        )
        def export_evidence(
            body: CanonicalEvidenceExportPayload | EvidenceExportPayload,
            request: Request,
            principal=export_guard,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            if isinstance(body, CanonicalEvidenceExportPayload):
                if not idempotency_key or not idempotency_key.strip():
                    raise HTTPException(status_code=422, detail="Idempotency-Key required")
                try:
                    source = SiteScoreEvidenceSelector(body.decision_id, body.expected_decision_version,
                        tuple(body.sections))
                    scope = EvidencePolicyScope(body.format, "authenticated-response",
                        _parse_time(body.from_time), _parse_time(body.to_time))
                    selected = GeneralDecisionEvidenceSelection(source, body.reason, scope, body.program_label)
                    return canonical_service(principal).export(principal, selected,
                        idempotency_key=idempotency_key, correlation_id=request.state.correlation_id,
                        expected_document_sha256=body.expected_document_sha256)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise canonical_error(exc) from exc
            if live_data_required():
                raise HTTPException(status_code=503, detail="CANONICAL_EVIDENCE_INPUT_REQUIRED")
            try:
                identity_boundary = _identity_boundary(principal)
                export_request = EvidenceExportRequest(
                    program_id=body.program_id,
                    purpose=body.purpose,
                    requested_by=principal.subject_id,
                    from_time=_parse_time(body.from_time),
                    to_time=_parse_time(body.to_time),
                    correlation_ids=tuple(body.correlation_ids),
                    export_scope=body.export_scope,
                    environment=body.environment,
                    build_version=body.build_version,
                    data_classification=body.data_classification,
                    sensitive=body.sensitive,
                    purpose_scope=body.purpose_scope,
                    expires_at=(_parse_time(body.expires_at) if body.expires_at else None),
                    authorized_by=body.authorized_by,
                    authorization_id=body.authorization_id,
                    masking_profile=body.masking_profile,
                    identity_boundary=identity_boundary,
                )
                bundle = export_service.export(
                    export_request,
                    decision_cards=tuple(
                        decision_card_from_mapping(card) for card in body.decision_cards
                    ),
                )
            except (AuditEvidenceExportError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            except RuntimeError as exc:
                correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
                logger.exception(
                    "Unexpected runtime error during evidence export (correlation_id=%s): %s",
                    correlation_id,
                    exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Audit evidence export failed; correlation_id={correlation_id}",
                ) from exc
            payload = bundle.to_dict()
            payload["correlation_id"] = request.state.correlation_id
            payload["identity_boundary_subject"] = principal.subject_id
            return payload

        @router.get("/evidence/exports")
        def list_retained_evidence(
            program_id: str | None = None, principal=view_guard
        ) -> dict[str, Any]:
            if live_data_required() and canonical_service_for_tenant is not None:
                try:
                    rows = GeneralSiteScoreRetainedReader(canonical_service(principal)).history(principal)
                    return {"exports": [row for row in rows if program_id is None or row["decision_id"] == program_id]}
                except HTTPException:
                    raise
                except Exception as exc:
                    raise canonical_error(exc) from exc
            if evidence_store is None:
                return {"exports": []}
            records = (
                evidence_store.list_for_program(program_id)
                if program_id is not None
                else evidence_store.list_all()
            )
            return {
                "exports": [
                    record.summary() for record in records if _belongs_to(record, principal)
                ]
            }

        def owned_record(export_id: str, principal: Principal) -> RetainedEvidence:
            try:
                record = None if evidence_store is None else evidence_store.get(export_id)
            except EvidenceIntegrityError as exc:
                # Ownership cannot be trusted until the stored record verifies.
                # Match unknown/foreign IDs instead of exposing a corrupt ID.
                raise HTTPException(
                    status_code=404, detail="retained evidence bundle not found"
                ) from exc
            if record is None or not _belongs_to(record, principal):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="retained evidence bundle not found",
                )
            return record

        def readable_content(export_id: str, principal: Principal) -> RetainedEvidence:
            # JSON contains the full bundle too: it is not a metadata-history
            # shortcut around the existing download admission/authority gates.
            if live_data_required():
                raise HTTPException(
                    status_code=503, detail="CANONICAL_EVIDENCE_DOWNLOAD_ADMISSION_REQUIRED"
                )
            record = owned_record(export_id, principal)
            if record.sensitive or record.data_classification.strip().lower() in {
                "restricted", "highly_restricted", "secret", "top_secret",
            }:
                raise HTTPException(
                    status_code=403, detail="SENSITIVE_EVIDENCE_DOWNLOAD_AUTHORITY_REQUIRED"
                )
            try:
                validate_retained_evidence_artifact(record)
                expires_at = record.bundle["export_governance"].get("expires_at")
                if expires_at is not None:
                    if not isinstance(expires_at, str):
                        raise EvidenceArtifactUnavailable("invalid evidence authorization expiry")
                    if _parse_time(expires_at) <= datetime.now(UTC):
                        raise EvidenceArtifactUnavailable("evidence download authorization expired")
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=410, detail="retained evidence content is unavailable"
                ) from exc
            # retain_until governs purge eligibility, not an invented access
            # expiry. Existing legal holds and tombstone ownership stay intact.
            return record

        def record_content_served(
            record: RetainedEvidence, request: Request, principal: Principal,
            *, content: bytes, artifact_format: str,
        ) -> None:
            active_audit_log.record(
                AuditEvent(
                    event_type="audit.evidence_download.v1",
                    actor=principal.subject_id,
                    action="export",
                    resource=f"audit-evidence/{record.program_id}/{record.export_id}",
                    outcome="served",
                    correlation_id=request.state.correlation_id,
                    metadata={
                        "export_id": record.export_id,
                        "bundle_checksum": record.bundle_checksum,
                        "artifact_sha256": hashlib.sha256(content).hexdigest(),
                        "artifact_bytes": len(content),
                        "format": artifact_format,
                        "identity_boundary": _identity_boundary(principal),
                        "download_evidence_id": record.bundle["export_governance"].get("download_evidence_id"),
                    },
                )
            )

        @router.get("/evidence/exports/{export_id}", response_model=dict[str, Any])
        def get_retained_evidence(
            export_id: str, request: Request, principal=export_guard,
        ) -> Response:
            if live_data_required() and canonical_service_for_tenant is not None:
                return Response(content=canonical_bytes(export_id, request, principal),
                    media_type="application/json", headers={"Cache-Control": "private, no-store",
                        "X-Content-Type-Options": "nosniff"})
            record = readable_content(export_id, principal)
            response = JSONResponse(
                content=record.to_dict(),
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            )
            record_content_served(
                record, request, principal, content=response.body, artifact_format="JSON",
            )
            return response

        @router.get(
            "/evidence/exports/{export_id}/download",
            response_class=Response,
            responses={
                200: {
                    "description": "Verified retained evidence PDF or JSON/CSV ZIP",
                    "content": {
                        "application/zip": {"schema": {"type": "string", "format": "binary"}},
                        "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                        "application/json": {"schema": {"type": "object"}},
                    },
                    "headers": {
                        "X-Artifact-SHA256": {
                            "schema": {"type": "string"},
                            "description": "SHA-256 of response bytes",
                        }
                    },
                },
                403: {"description": "Export permission or sensitive-data authority required"},
                404: {"description": "No evidence owned by this subject and tenant"},
                410: {"description": "Expired authorization, purged or unavailable artifact"},
                503: {"description": "Canonical production download admission required"},
            },
        )
        def download_retained_evidence(
            export_id: str,
            request: Request,
            format: Literal["zip", "pdf", "json", "csv"] = "zip",
            principal=export_guard,
        ) -> Response:
            if live_data_required() and canonical_service_for_tenant is not None:
                selected_format = "csv" if format in ("zip", "csv") else format
                try:
                    artifact = GeneralSiteScoreRetainedReader(canonical_service(principal)).artifact(
                        principal, export_id, correlation_id=request.state.correlation_id, format=selected_format)
                except HTTPException:
                    raise
                except EvidenceArtifactUnavailable as exc:
                    raise HTTPException(status_code=503, detail="CANONICAL_EVIDENCE_INPUT_REQUIRED:artifact_render_unavailable") from exc
                except Exception as exc:
                    raise canonical_error(exc) from exc
                extension = {"json": "json", "csv": "zip", "pdf": "pdf"}[selected_format]
                return Response(content=artifact.content, media_type=artifact.media_type, headers={
                    "Content-Disposition": f'attachment; filename="audit-evidence.{extension}"',
                    "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                    "X-Artifact-SHA256": artifact.sha256})
            if not live_data_required() and format not in ("zip", "pdf"):
                raise HTTPException(status_code=422, detail="unsupported legacy evidence format")
            record = readable_content(export_id, principal)
            try:
                artifact = (
                    render_evidence_pdf(record)
                    if format == "pdf"
                    else render_evidence_archive(record)
                )
            except EvidenceArtifactUnavailable as exc:
                raise HTTPException(status_code=410, detail=str(exc)) from exc
            record_content_served(
                record, request, principal, content=artifact.content,
                artifact_format="PDF" if format == "pdf" else "JSON/CSV ZIP",
            )
            # The filename contains no caller-controlled ID. This is an
            # authenticated response, not a public or signed URL.
            return Response(
                content=artifact.content,
                media_type=artifact.media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="audit-evidence.{format}"',
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                    "X-Artifact-SHA256": artifact.sha256,
                },
            )

        @router.post("/evidence/exports/{export_id}/legal-hold")
        def apply_legal_hold(
            export_id: str,
            body: EvidenceGovernancePayload,
            request: Request,
            principal=update_guard,
        ) -> dict[str, Any]:
            if evidence_store is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="retained evidence store not configured",
                )
            _assert_governance_role(principal.roles, body.role)
            try:
                record = evidence_store.apply_legal_hold(
                    export_id,
                    context=GovernedEvidenceOperation(
                        actor=principal.subject_id,
                        role=body.role,
                        reason=body.reason,
                        correlation_id=body.correlation_id or request.state.correlation_id,
                    ),
                )
            except KeyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="retained evidence bundle not found",
                ) from exc
            except EvidenceGovernanceError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            return record.summary()

        @router.post("/evidence/retention/purge")
        def purge_retained_evidence(
            body: EvidenceRetentionPurgePayload,
            request: Request,
            principal=delete_guard,
        ) -> dict[str, Any]:
            if evidence_store is None:
                return {"purged_export_ids": []}
            _assert_governance_role(principal.roles, body.role)
            try:
                purged = evidence_store.purge_expired(
                    _parse_time(body.as_of),
                    context=GovernedEvidenceOperation(
                        actor=principal.subject_id,
                        role=body.role,
                        reason=body.reason,
                        correlation_id=body.correlation_id or request.state.correlation_id,
                    ),
                )
            except EvidenceGovernanceError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            return {"purged_export_ids": purged}

        @router.get(
            "/evidence/retention/expired",
        )
        def list_expired_retained_evidence(as_of: str, principal=view_guard) -> dict[str, Any]:
            if evidence_store is None:
                return {"exports": []}
            records = evidence_store.list_expired(_parse_time(as_of))
            return {
                "exports": [
                    record.summary() for record in records if _belongs_to(record, principal)
                ]
            }

        return router

    def _identity_boundary(principal: Principal) -> str:
        return "http-principal:" + canonical_json(
            {
                "subject_id": principal.subject_id,
                "tenant_id": principal.tenant_id,
            }
        )

    def _belongs_to(record: RetainedEvidence, principal: Principal) -> bool:
        governance = record.bundle.get("export_governance")
        return (
            record.requested_by == principal.subject_id
            and isinstance(governance, dict)
            and governance.get("identity_boundary") == _identity_boundary(principal)
        )

    def _assert_governance_role(roles: frozenset[Role], requested_role: str) -> None:
        allowed = frozenset(
            governance_role
            for role in roles
            for governance_role in _GOVERNANCE_ROLES_BY_PLATFORM_ROLE.get(role, frozenset())
        )
        if requested_role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="platform role does not permit requested evidence governance role",
            )

    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    __all__ = ["create_audit_router"]
