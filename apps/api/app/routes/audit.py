"""Audit evidence export API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.opsboard.audit import AuditEvidenceExportError, AuditEvidenceExportService
from modules.opsboard.audit.application.evidence_export import decision_card_from_mapping
from modules.opsboard.audit.domain.evidence import EvidenceExportRequest
from shared.audit import EvidenceGovernanceError, InMemoryAuditLog
from shared.audit.persistence import EvidenceBundleStore, GovernedEvidenceOperation
from shared.auth import Role

try:
    from fastapi import APIRouter, Depends, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:

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
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        router = APIRouter(prefix="/audit", tags=["audit"])
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        export_guard = Depends(
            require_permission("audit", Action.EXPORT, engine=authz_engine)
        )
        view_guard = Depends(
            require_permission("audit", Action.VIEW, engine=authz_engine)
        )
        update_guard = Depends(
            require_permission("audit", Action.UPDATE, engine=authz_engine)
        )
        delete_guard = Depends(
            require_permission("audit", Action.DELETE, engine=authz_engine)
        )
        export_service = service or AuditEvidenceExportService(
            audit_log=active_audit_log, evidence_store=evidence_store
        )

        @router.post("/evidence/export", status_code=status.HTTP_201_CREATED)
        def export_evidence(
            body: EvidenceExportPayload,
            request: Request,
            principal=export_guard,
        ) -> dict[str, Any]:
            try:
                identity_boundary = (
                    body.identity_boundary
                    or f"http-principal:{principal.subject_id}"
                )
                export_request = EvidenceExportRequest(
                    program_id=body.program_id,
                    purpose=body.purpose,
                    requested_by=body.requested_by,
                    from_time=_parse_time(body.from_time),
                    to_time=_parse_time(body.to_time),
                    correlation_ids=tuple(body.correlation_ids),
                    export_scope=body.export_scope,
                    environment=body.environment,
                    build_version=body.build_version,
                    data_classification=body.data_classification,
                    sensitive=body.sensitive,
                    purpose_scope=body.purpose_scope,
                    expires_at=(
                        _parse_time(body.expires_at) if body.expires_at else None
                    ),
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
            except (AuditEvidenceExportError, KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            payload = bundle.to_dict()
            payload["correlation_id"] = request.state.correlation_id
            payload["identity_boundary_subject"] = principal.subject_id
            return payload

        @router.get("/evidence/exports", dependencies=[view_guard])
        def list_retained_evidence(program_id: str | None = None) -> dict[str, Any]:
            if evidence_store is None:
                return {"exports": []}
            records = (
                evidence_store.list_for_program(program_id)
                if program_id is not None
                else evidence_store.list_all()
            )
            return {"exports": [record.summary() for record in records]}

        @router.get("/evidence/exports/{export_id}", dependencies=[view_guard])
        def get_retained_evidence(export_id: str) -> dict[str, Any]:
            record = None if evidence_store is None else evidence_store.get(export_id)
            if record is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="retained evidence bundle not found",
                )
            return record.to_dict()

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
                        correlation_id=body.correlation_id
                        or request.state.correlation_id,
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
                        correlation_id=body.correlation_id
                        or request.state.correlation_id,
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
            dependencies=[view_guard],
        )
        def list_expired_retained_evidence(as_of: str) -> dict[str, Any]:
            if evidence_store is None:
                return {"exports": []}
            records = evidence_store.list_expired(_parse_time(as_of))
            return {"exports": [record.summary() for record in records]}

        return router

    def _assert_governance_role(roles: frozenset[Role], requested_role: str) -> None:
        allowed = frozenset(
            governance_role
            for role in roles
            for governance_role in _GOVERNANCE_ROLES_BY_PLATFORM_ROLE.get(
                role, frozenset()
            )
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
