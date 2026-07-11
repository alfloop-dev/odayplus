"""Audit evidence export API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.opsboard.audit import AuditEvidenceExportError, AuditEvidenceExportService
from modules.opsboard.audit.application.evidence_export import decision_card_from_mapping
from modules.opsboard.audit.domain.evidence import EvidenceExportRequest
from shared.audit import InMemoryAuditLog
from shared.audit.persistence import EvidenceBundleStore

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
        export_service = service or AuditEvidenceExportService(
            audit_log=active_audit_log, evidence_store=evidence_store
        )

        @router.post("/evidence/export", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("audit", Action.EXPORT, engine=authz_engine))])
        def export_evidence(body: EvidenceExportPayload, request: Request) -> dict[str, Any]:
            try:
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
            return payload

        @router.get("/evidence/exports", dependencies=[Depends(require_permission("audit", Action.VIEW, engine=authz_engine))])
        def list_retained_evidence(program_id: str | None = None) -> dict[str, Any]:
            if evidence_store is None:
                return {"exports": []}
            records = (
                evidence_store.list_for_program(program_id)
                if program_id is not None
                else evidence_store.list_all()
            )
            return {"exports": [record.summary() for record in records]}

        @router.get("/evidence/exports/{export_id}", dependencies=[Depends(require_permission("audit", Action.VIEW, engine=authz_engine))])
        def get_retained_evidence(export_id: str) -> dict[str, Any]:
            record = None if evidence_store is None else evidence_store.get(export_id)
            if record is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="retained evidence bundle not found",
                )
            return record.to_dict()

        return router

    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    __all__ = ["create_audit_router"]
