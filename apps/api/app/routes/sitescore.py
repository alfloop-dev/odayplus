from __future__ import annotations

from typing import Any
from uuid import uuid4

from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, Header, HTTPException, Request, status, Depends
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.sitescore.application.reporting import SiteScoreReportService
    from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
    from shared.workflow.sitescore import (
        CandidateSiteRealizationHook,
        DecisionAction,
        SiteScoreDecisionError,
        SiteScoreDecisionWorkflow,
    )


    class SiteScoreScoreJobPayload(BaseModel):
        features: list[dict[str, Any]] = Field(default_factory=list)
        prediction_origin_time: str | None = None
        idempotency_key: str | None = None


    class OpenDecisionPayload(BaseModel):
        report_id: str
        created_by: str = Field(min_length=1)


    class DecisionPayload(BaseModel):
        action: str
        actor: str = Field(min_length=1)
        reason: str = ""


    def create_sitescore_router(
        *,
        repository: InMemorySiteScoreRepository | None = None,
        workflow: SiteScoreDecisionWorkflow | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ) -> APIRouter:
        from shared.auth import Action
        from apps.api.oday_api.security.dependencies import build_engine, require_permission

        router = APIRouter(prefix="/sitescore", tags=["sitescore"])
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        site_repository = repository or InMemorySiteScoreRepository()
        realization_hook = CandidateSiteRealizationHook()
        decision_workflow = workflow or SiteScoreDecisionWorkflow(
            audit_log=active_audit_log, hooks=[realization_hook]
        )
        if decision_workflow is workflow:
            decision_workflow.register_hook(realization_hook)
        service = SiteScoreReportService(repository=site_repository)
        idempotency_index: dict[str, str] = {}
        jobs: dict[str, dict[str, Any]] = {}

        @router.post("/score-jobs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("sitescore", Action.EXECUTE, engine=authz_engine))])
        @router.post("/reports", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("sitescore", Action.EXECUTE, engine=authz_engine))])
        def create_score_job(
            body: SiteScoreScoreJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            if effective_key and effective_key in idempotency_index:
                payload = dict(jobs[idempotency_index[effective_key]])
                payload["created"] = False
                return payload

            reports = service.score_candidates(
                body.features,
                prediction_origin_time=_parse_origin(body.prediction_origin_time),
            )
            job_id = f"sitescore-score-{uuid4()}"
            audit_event = active_audit_log.record(
                AuditEvent(
                    event_type="sitescore.scored.v1",
                    actor="system",
                    action="run_model",
                    resource="sitescore/score-job",
                    outcome="accepted",
                    correlation_id=request.state.correlation_id,
                    job_id=job_id,
                    metadata={
                        "idempotency_key": effective_key,
                        "candidate_count": len(reports),
                    },
                )
            )
            payload = {
                "job_id": job_id,
                "status": "succeeded",
                "reports": [report.to_dict() for report in reports],
                "summaries": [report.to_summary_dict() for report in reports],
                "audit_event_id": audit_event.event_id,
                "correlation_id": request.state.correlation_id,
                "created": True,
            }
            jobs[job_id] = payload
            if effective_key:
                idempotency_index[effective_key] = job_id
            return payload

        @router.get("/reports", dependencies=[Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))])
        def list_reports() -> dict[str, Any]:
            reports = site_repository.list_latest()
            return {
                "items": [report.to_summary_dict() for report in reports],
                "count": len(reports),
            }

        @router.get("/reports/{candidate_site_id}", dependencies=[Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))])
        def candidate_reports(candidate_site_id: str) -> dict[str, Any]:
            latest = site_repository.latest(candidate_site_id)
            if latest is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
            history = site_repository.history(candidate_site_id)
            return {
                "latest": latest.to_dict(),
                "versions": [report.to_dict() for report in history],
                "version_count": len(history),
            }

        @router.post("/decisions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("sitescore", Action.EXECUTE, engine=authz_engine))])
        def open_decision(body: OpenDecisionPayload, request: Request) -> dict[str, Any]:
            report = site_repository.get_report(body.report_id)
            if report is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
            decision = decision_workflow.open_decision(
                report,
                created_by=body.created_by,
                correlation_id=request.state.correlation_id,
            )
            decision = decision_workflow.submit_for_review(
                decision.decision_id,
                submitted_by=body.created_by,
                correlation_id=request.state.correlation_id,
            )
            return decision.to_dict()

        @router.post("/decisions/{decision_id}/decision", dependencies=[Depends(require_permission("sitescore", Action.APPROVE, engine=authz_engine))])
        def decide(decision_id: str, body: DecisionPayload, request: Request) -> dict[str, Any]:
            try:
                action = DecisionAction(body.action)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            try:
                outcome = decision_workflow.decide(
                    decision_id,
                    action=action,
                    actor=body.actor,
                    reason=body.reason,
                    correlation_id=request.state.correlation_id,
                )
            except SiteScoreDecisionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            payload = outcome.to_dict()
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get("/decisions/{decision_id}", dependencies=[Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))])
        def get_decision(decision_id: str) -> dict[str, Any]:
            decision = decision_workflow.get(decision_id)
            if decision is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="decision not found")
            return decision.to_dict()

        @router.get("/realized", dependencies=[Depends(require_permission("sitescore", Action.VIEW, engine=authz_engine))])
        def list_realized() -> dict[str, Any]:
            realized = realization_hook.list_realized()
            return {
                "items": [site.to_dict() for site in realized],
                "count": len(realized),
            }

        return router

    def _parse_origin(value: str | None) -> Any:
        if value is None:
            return None
        from datetime import UTC, datetime

        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    __all__ = [
        "DecisionPayload",
        "OpenDecisionPayload",
        "SiteScoreScoreJobPayload",
        "create_sitescore_router",
    ]
