from __future__ import annotations

from typing import Any

from modules.avm.application.valuation import AVMError
from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.avm.application import AVMService
    from modules.avm.infrastructure import InMemoryAVMRepository


    class AVMCasePayload(BaseModel):
        store_id: str = Field(min_length=1)
        gm_ttm: float
        forecast_gm_next_12m: float
        asset_book_value: float
        equipment_fair_value: float
        lease_liability: float = 0.0
        working_capital: float = 0.0
        comparable_multiples: list[float] = Field(default_factory=list)
        liquidity_discount: float = 0.1
        quality_score: float = 1.0
        source_snapshot_ids: list[str] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        created_by: str = Field(min_length=1)
        idempotency_key: str | None = None


    class ActorPayload(BaseModel):
        actor: str = Field(min_length=1)


    class FinanceApprovalPayload(BaseModel):
        actor: str = Field(min_length=1)
        reason: str = ""
        reserve_price: float | None = None


    class DataRoomExportPayload(BaseModel):
        actor: str = Field(min_length=1)
        reason: str = ""


    class AVMCaseStore:
        def __init__(self) -> None:
            self._idempotency_index: dict[str, str] = {}

        def put(self, idempotency_key: str | None, case_id: str) -> None:
            if idempotency_key:
                self._idempotency_index[idempotency_key] = case_id

        def get(self, idempotency_key: str | None) -> str | None:
            if not idempotency_key:
                return None
            return self._idempotency_index.get(idempotency_key)


    def create_avm_router(
        *,
        repository: InMemoryAVMRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        case_store: AVMCaseStore | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        router = APIRouter(prefix="/avm", tags=["avm"])
        service = AVMService(repository=repository or InMemoryAVMRepository())
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        idempotency = case_store or AVMCaseStore()

        def _audit(
            *,
            event_type: str,
            actor: str,
            action: str,
            resource: str,
            outcome: str,
            request: Request,
            metadata: dict[str, Any] | None = None,
        ) -> str:
            return active_audit_log.record(
                AuditEvent(
                    event_type=event_type,
                    actor=actor,
                    action=action,
                    resource=resource,
                    outcome=outcome,
                    correlation_id=request.state.correlation_id,
                    metadata=metadata or {},
                )
            ).event_id

        def _run(fn):
            try:
                return fn()
            except AVMError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc

        @router.post("/cases", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("avm", Action.CREATE, engine=authz_engine))])
        def create_case(
            body: AVMCasePayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            existing_case_id = idempotency.get(effective_key)
            if existing_case_id:
                case = service.get_case(existing_case_id)
                payload = case.to_dict() if case else {}
                payload["created"] = False
                return payload
            case = service.create_case(
                body.model_dump(exclude={"created_by", "idempotency_key"}),
                created_by=body.created_by,
                correlation_id=request.state.correlation_id,
            )
            idempotency.put(effective_key, case.case_id)
            payload = case.to_dict()
            payload["created"] = True
            payload["correlation_id"] = request.state.correlation_id
            payload["audit_event_id"] = _audit(
                event_type="avm.case_created.v1",
                actor=body.created_by,
                action="create_case",
                resource=f"avm/cases/{case.case_id}",
                outcome="created",
                request=request,
                metadata={"idempotency_key": effective_key},
            )
            return payload

        @router.get("/cases", dependencies=[Depends(require_permission("avm", Action.VIEW, engine=authz_engine))])
        def list_cases() -> dict[str, Any]:
            items = service.repository.list_cases()
            return {"items": [item.to_dict() for item in items], "count": len(items)}

        @router.get("/cases/{case_id}", dependencies=[Depends(require_permission("avm", Action.VIEW, engine=authz_engine))])
        def get_case(case_id: str) -> dict[str, Any]:
            case = service.get_case(case_id)
            if case is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found")
            return case.to_dict()

        @router.post("/cases/{case_id}/normalize", dependencies=[Depends(require_permission("avm", Action.CREATE, engine=authz_engine))])
        def normalize(case_id: str, body: ActorPayload, request: Request) -> dict[str, Any]:
            margin = _run(
                lambda: service.normalize(
                    case_id, actor=body.actor, correlation_id=request.state.correlation_id
                )
            )
            _audit(
                event_type="avm.normalized.v1",
                actor=body.actor,
                action="normalize",
                resource=f"avm/cases/{case_id}",
                outcome="completed",
                request=request,
            )
            return margin.to_dict()

        @router.post("/cases/{case_id}/value", dependencies=[Depends(require_permission("avm", Action.CREATE, engine=authz_engine))])
        def value(case_id: str, body: ActorPayload, request: Request) -> dict[str, Any]:
            report = _run(
                lambda: service.value(
                    case_id, actor=body.actor, correlation_id=request.state.correlation_id
                )
            )
            payload = report.to_dict()
            payload["audit_event_id"] = _audit(
                event_type="avm.valued.v1",
                actor=body.actor,
                action="value",
                resource=f"avm/cases/{case_id}/report",
                outcome="review_required",
                request=request,
                metadata={
                    "report_id": report.report_id,
                    "valuation_version": report.valuation_version,
                    "confidence": report.confidence,
                },
            )
            return payload

        @router.post("/cases/{case_id}/finance-approval", dependencies=[Depends(require_permission("avm", Action.APPROVE, engine=authz_engine))])
        def approve_finance(
            case_id: str, body: FinanceApprovalPayload, request: Request
        ) -> dict[str, Any]:
            report = _run(
                lambda: service.approve_finance(
                    case_id,
                    actor=body.actor,
                    reason=body.reason,
                    reserve_price=body.reserve_price,
                    correlation_id=request.state.correlation_id,
                )
            )
            payload = report.to_dict()
            payload["audit_event_id"] = _audit(
                event_type="avm.finance_approved.v1",
                actor=body.actor,
                action="approve_finance",
                resource=f"avm/cases/{case_id}/finance-approval",
                outcome="approved",
                request=request,
                metadata={
                    "reason": body.reason,
                    "decision_id": report.finance_approval.decision_id
                    if report.finance_approval
                    else None,
                    "reserve_price": report.finance_approval.reserve_price
                    if report.finance_approval
                    else None,
                    "reserve_overridden": (
                        report.finance_approval.reserve_price != report.reserve_price
                        if report.finance_approval
                        else False
                    ),
                    "valuation_version": report.valuation_version,
                },
            )
            return payload

        @router.post("/cases/{case_id}/dataroom", dependencies=[Depends(require_permission("avm", Action.CREATE, engine=authz_engine))])
        def build_dataroom(case_id: str, body: ActorPayload, request: Request) -> dict[str, Any]:
            dataroom = _run(
                lambda: service.build_dataroom(
                    case_id, actor=body.actor, correlation_id=request.state.correlation_id
                )
            )
            payload = dataroom.to_dict()
            payload["audit_event_id"] = _audit(
                event_type="avm.dataroom_ready.v1",
                actor=body.actor,
                action="build_dataroom",
                resource=f"avm/cases/{case_id}/dataroom",
                outcome="ready",
                request=request,
                metadata={
                    "dataroom_id": dataroom.dataroom_id,
                    "completeness": dataroom.completeness,
                    "missing_documents": list(dataroom.missing_documents),
                },
            )
            return payload

        @router.get("/cases/{case_id}/dataroom", dependencies=[Depends(require_permission("avm", Action.VIEW, engine=authz_engine))])
        def get_dataroom(case_id: str) -> dict[str, Any]:
            dataroom = service.dataroom(case_id)
            if dataroom is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="dataroom not found",
                )
            return dataroom.to_dict()

        @router.post("/cases/{case_id}/dataroom/export", dependencies=[Depends(require_permission("avm", Action.EXPORT, engine=authz_engine))])
        def export_dataroom(
            case_id: str, body: DataRoomExportPayload, request: Request
        ) -> dict[str, Any]:
            dataroom = _run(
                lambda: service.export_dataroom(
                    case_id,
                    actor=body.actor,
                    reason=body.reason,
                    correlation_id=request.state.correlation_id,
                )
            )
            payload = dataroom.to_dict()
            payload["audit_event_id"] = _audit(
                event_type="avm.dataroom_exported.v1",
                actor=body.actor,
                action="export_dataroom",
                resource=f"avm/cases/{case_id}/dataroom/export",
                outcome="exported",
                request=request,
                metadata={
                    "reason": body.reason,
                    "dataroom_id": dataroom.dataroom_id,
                    "export_count": len(dataroom.export_audit),
                    "completeness": dataroom.completeness,
                },
            )
            return payload

        @router.get("/cases/{case_id}/reports", dependencies=[Depends(require_permission("avm", Action.VIEW, engine=authz_engine))])
        def reports(case_id: str) -> dict[str, Any]:
            case = service.get_case(case_id)
            if case is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found")
            items = service.report_history(case_id)
            return {
                "items": [item.to_dict() for item in items],
                "count": len(items),
                "latest_version": items[-1].valuation_version if items else None,
            }

        @router.get("/cases/{case_id}/report", dependencies=[Depends(require_permission("avm", Action.VIEW, engine=authz_engine))])
        def report(case_id: str) -> dict[str, Any]:
            latest = service.latest_report(case_id)
            if latest is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="report not found"
                )
            return latest.to_dict()

        return router


    __all__ = [
        "AVMCasePayload",
        "AVMCaseStore",
        "ActorPayload",
        "DataRoomExportPayload",
        "FinanceApprovalPayload",
        "create_avm_router",
    ]
