from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from modules.netplan.application import (
        NetPlanApprovalError,
        NetPlanNotFoundError,
        NetPlanService,
    )
    from modules.netplan.infrastructure import InMemoryNetPlanRepository
    from solver.netplan import NetPlanConstraints


    class NetPlanScenarioPayload(BaseModel):
        tenant_id: str = Field(min_length=1)
        scenario_name: str = Field(min_length=1)
        planning_horizon: str = Field(min_length=1)
        constraints: dict[str, Any]
        existing_stores: list[dict[str, Any]] = Field(default_factory=list)
        candidate_sites: list[dict[str, Any]] = Field(default_factory=list)
        scenario_id: str | None = None
        created_at: str | None = None


    class NetPlanActorPayload(BaseModel):
        actor: str = Field(default="system", min_length=1)
        reason: str = ""
        occurred_at: str | None = None


    class NetPlanSolvePayload(BaseModel):
        actor: str = Field(default="system", min_length=1)
        reason: str = "netplan constrained network solve"
        solved_at: str | None = None
        alternative_limit: int = Field(default=3, ge=1, le=20)


    class NetPlanDecisionPayload(BaseModel):
        actor_id: str = Field(min_length=1)
        reason: str = Field(min_length=1)
        decision: str = "approved"
        decided_at: str | None = None


    class NetPlanExecutionPayload(BaseModel):
        executed_by: str = Field(default="system", min_length=1)
        executed_at: str | None = None


    class NetPlanOutcomePayload(BaseModel):
        actual_gross_margin: float
        actor: str = Field(default="system", min_length=1)
        observed_at: str | None = None
        source_snapshot_ids: list[str] = Field(default_factory=list)


    def create_netplan_router(
        *,
        repository: InMemoryNetPlanRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        router = APIRouter(prefix="/netplan", tags=["netplan"])
        service = NetPlanService(repository=repository or InMemoryNetPlanRepository())
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)

        @router.post("/scenarios", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("netplan", Action.CREATE, engine=authz_engine))])
        def create_scenario(body: NetPlanScenarioPayload, request: Request) -> dict[str, Any]:
            try:
                scenario = service.create_scenario(
                    tenant_id=body.tenant_id,
                    scenario_name=body.scenario_name,
                    planning_horizon=body.planning_horizon,
                    constraints=NetPlanConstraints.from_mapping(body.constraints),
                    existing_stores=body.existing_stores,
                    candidate_sites=body.candidate_sites,
                    scenario_id=body.scenario_id,
                    correlation_id=request.state.correlation_id,
                    created_at=_parse_time(body.created_at),
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            payload = scenario.to_dict()
            payload["audit_event_id"] = _record_audit(
                active_audit_log,
                request,
                event_type="netplan.scenario_created.v1",
                actor="system",
                action="create_scenario",
                resource=f"netplan/scenarios/{scenario.scenario_id}",
                outcome="created",
                metadata={"tenant_id": scenario.tenant_id},
            )
            return payload

        @router.get("/scenarios", dependencies=[Depends(require_permission("netplan", Action.VIEW, engine=authz_engine))])
        def list_scenarios() -> dict[str, Any]:
            scenarios = service.repository.list_scenarios()
            return {"items": [scenario.to_dict() for scenario in scenarios], "count": len(scenarios)}

        @router.get("/scenarios/{scenario_id}", dependencies=[Depends(require_permission("netplan", Action.VIEW, engine=authz_engine))])
        def get_scenario(scenario_id: str) -> dict[str, Any]:
            scenario = service.repository.get_scenario(scenario_id)
            if scenario is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scenario not found")
            return _scenario_detail(service, scenario_id, scenario.to_dict())

        @router.post("/scenarios/{scenario_id}/solve", dependencies=[Depends(require_permission("netplan", Action.EXECUTE, engine=authz_engine))])
        def solve(scenario_id: str, body: NetPlanSolvePayload, request: Request) -> dict[str, Any]:
            return _run(
                lambda: service.solve(
                    scenario_id,
                    actor=body.actor,
                    reason=body.reason,
                    solved_at=_parse_time(body.solved_at),
                    alternative_limit=body.alternative_limit,
                ),
                active_audit_log,
                request,
                "netplan.solved.v1",
                body.actor,
                "run_solver",
                f"netplan/scenarios/{scenario_id}/solve",
                scenario_id,
            )

        @router.post("/scenarios/{scenario_id}/submit", dependencies=[Depends(require_permission("netplan", Action.CREATE, engine=authz_engine))])
        def submit(scenario_id: str, body: NetPlanActorPayload, request: Request) -> dict[str, Any]:
            return _run(
                lambda: service.submit_for_approval(
                    scenario_id,
                    actor=body.actor,
                    reason=body.reason or "submitted for network planning approval",
                    occurred_at=_parse_time(body.occurred_at),
                ),
                active_audit_log,
                request,
                "netplan.submitted.v1",
                body.actor,
                "submit",
                f"netplan/scenarios/{scenario_id}/submit",
                scenario_id,
            )

        @router.post("/scenarios/{scenario_id}/decide", dependencies=[Depends(require_permission("netplan", Action.APPROVE, engine=authz_engine))])
        def decide(scenario_id: str, body: NetPlanDecisionPayload, request: Request) -> dict[str, Any]:
            return _run(
                lambda: service.decide(
                    scenario_id,
                    actor_id=body.actor_id,
                    reason=body.reason,
                    decision=body.decision,
                    decided_at=_parse_time(body.decided_at),
                ),
                active_audit_log,
                request,
                "netplan.approved.v1" if body.decision.lower() == "approved" else "netplan.rejected.v1",
                body.actor_id,
                body.decision.lower(),
                f"netplan/scenarios/{scenario_id}/decide",
                scenario_id,
            )

        @router.post("/scenarios/{scenario_id}/execute", dependencies=[Depends(require_permission("netplan", Action.EXECUTE, engine=authz_engine))])
        def execute(
            scenario_id: str, body: NetPlanExecutionPayload, request: Request
        ) -> dict[str, Any]:
            return _run(
                lambda: service.execute(
                    scenario_id,
                    executed_by=body.executed_by,
                    executed_at=_parse_time(body.executed_at),
                ),
                active_audit_log,
                request,
                "netplan.executed.v1",
                body.executed_by,
                "execute",
                f"netplan/scenarios/{scenario_id}/execute",
                scenario_id,
            )

        @router.post("/scenarios/{scenario_id}/outcomes", dependencies=[Depends(require_permission("netplan", Action.EXECUTE, engine=authz_engine))])
        def record_outcome(
            scenario_id: str, body: NetPlanOutcomePayload, request: Request
        ) -> dict[str, Any]:
            return _run(
                lambda: service.record_outcome(
                    scenario_id,
                    actual_gross_margin=body.actual_gross_margin,
                    observed_at=_parse_time(body.observed_at),
                    source_snapshot_ids=body.source_snapshot_ids,
                    actor=body.actor,
                ),
                active_audit_log,
                request,
                "netplan.outcome_observed.v1",
                body.actor,
                "record_outcome",
                f"netplan/scenarios/{scenario_id}/outcomes",
                scenario_id,
            )

        @router.post("/scenarios/{scenario_id}/close", dependencies=[Depends(require_permission("netplan", Action.EXECUTE, engine=authz_engine))])
        def close(scenario_id: str, body: NetPlanActorPayload, request: Request) -> dict[str, Any]:
            return _run(
                lambda: service.close(
                    scenario_id,
                    actor=body.actor,
                    reason=body.reason or "netplan outcome written to label registry",
                    occurred_at=_parse_time(body.occurred_at),
                ),
                active_audit_log,
                request,
                "netplan.closed.v1",
                body.actor,
                "close",
                f"netplan/scenarios/{scenario_id}/close",
                scenario_id,
            )

        return router


    def _scenario_detail(
        service: NetPlanService, scenario_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        solve = service.repository.get_solve(scenario_id)
        execution = service.repository.get_execution(scenario_id)
        outcome = service.repository.get_outcome(scenario_id)
        payload["solve"] = solve.to_dict() if solve else None
        payload["approvals"] = [
            approval.to_dict() for approval in service.repository.list_approvals(scenario_id)
        ]
        payload["execution"] = execution.to_dict() if execution else None
        payload["outcome"] = outcome.to_dict() if outcome else None
        return payload


    def _run(
        action: Any,
        audit_log: InMemoryAuditLog,
        request: Request,
        event_type: str,
        actor: str,
        audit_action: str,
        resource: str,
        scenario_id: str,
    ) -> dict[str, Any]:
        try:
            result = action()
        except NetPlanNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (NetPlanApprovalError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        event_id = _record_audit(
            audit_log,
            request,
            event_type=event_type,
            actor=actor,
            action=audit_action,
            resource=resource,
            outcome="accepted",
            metadata={"scenario_id": scenario_id},
        )
        payload = result.to_dict()
        payload["audit_event_id"] = event_id
        payload["correlation_id"] = request.state.correlation_id
        return payload


    def _record_audit(
        audit_log: InMemoryAuditLog,
        request: Request,
        *,
        event_type: str,
        actor: str,
        action: str,
        resource: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return audit_log.record(
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


    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed


    __all__ = [
        "NetPlanDecisionPayload",
        "NetPlanScenarioPayload",
        "create_netplan_router",
    ]
