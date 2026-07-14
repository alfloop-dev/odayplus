"""Growth workspace FastAPI sub-router.

Routes (all under /operator/growth):
  GET  /freshness          — data freshness + model version
  GET  /segments           — segment list with optional ?segment_id filter
  GET  /recommendations    — PriceOps recommendations with optional ?segment_id
  GET  /actions            — Growth Action list with optional ?segment_id / ?status
  GET  /actions/{id}       — single action detail + closeoutGate
  GET  /summary            — workspace summary counters
  POST /actions            — create draft (3 entry points, conflict gate enforced)
  POST /actions/{id}/transition   — lifecycle advance
  POST /actions/{id}/outcome      — effectiveness writeback

Auth: write endpoints require intervention CREATE guard passed from operator.py.
Idempotency-Key is handled per-endpoint and passed to the service.
X-Correlation-Id is read from request.state.correlation_id.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from modules.opsboard.application.growth import (
    GrowthCloseoutGateError,
    GrowthConflict,
    GrowthNotFound,
    GrowthPolicyError,
    GrowthService,
)

# ---------------------------------------------------------------------------
# Request / response DTOs
# ---------------------------------------------------------------------------


class CreateActionPayload(BaseModel):
    """POST /operator/growth/actions — create draft body.

    Supports all three creation entry points:
      • from PriceOps recommendation row  (sourceRecommendationId set)
      • from recommendations entry         (sourceRecommendationId set, payload-driven)
      • direct new-action entry            (no sourceRecommendationId)
    """

    model_config = ConfigDict(extra="allow")

    name: str
    segmentId: str
    objective: str
    targetLift: float
    kind: str = "offpeak"
    observationWindowDays: int = Field(default=14, ge=1, le=365)
    observationWindow: str | None = None
    store: str = "全品牌"
    channel: str = "LINE 推播"
    budget: float = 0
    rationale: str = ""
    rollbackPlan: str = ""
    sourceRecommendationId: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None

    @field_validator("name", "segmentId", "objective")
    @classmethod
    def non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v


class ConflictCheckPayload(BaseModel):
    """POST /operator/growth/conflicts/check — run the five conflict checks."""

    model_config = ConfigDict(extra="allow")

    kind: str = "offpeak"
    store: str = "全品牌"
    observationWindow: str = ""
    channel: str = "LINE 推播"
    budget: float = 0
    excludeActionId: str | None = None


class SubmitPayload(BaseModel):
    """POST /operator/growth/actions/{id}/submit — submit draft for approval."""

    actorRoleId: str | None = None
    actorName: str | None = None


class ApprovalDecisionPayload(BaseModel):
    """POST /operator/growth/approvals/{id}/decision — approve / reject."""

    decision: str
    reason: str = ""
    actorRoleId: str | None = None
    actorName: str | None = None

    @field_validator("decision")
    @classmethod
    def valid_decision(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"approved", "rejected"}:
            raise ValueError("decision must be 'approved' or 'rejected'")
        return v


class TransitionActionPayload(BaseModel):
    """POST /operator/growth/actions/{id}/transition — lifecycle advance."""

    targetStatus: str
    actorRoleId: str | None = None
    actorName: str | None = None

    @field_validator("targetStatus")
    @classmethod
    def non_empty(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("targetStatus must not be empty")
        return v


class OutcomePayload(BaseModel):
    """POST /operator/growth/actions/{id}/outcome — effectiveness writeback."""

    outcome: str
    requiredAction: str
    observedLift: float | None = None
    evidenceLevel: str = "medium"
    rationale: str = ""
    actorRoleId: str | None = None
    actorName: str | None = None

    @field_validator("outcome", "requiredAction")
    @classmethod
    def non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_growth_sub_router(
    service: GrowthService,
    *,
    require_permission_fn: Any = None,
) -> APIRouter:
    """Return the Growth sub-router wired to a shared GrowthService instance."""
    router = APIRouter(prefix="/growth", tags=["operator-growth"])

    # Build auth dependency list
    write_deps: list[Any] = []
    if require_permission_fn is not None:
        write_deps = [Depends(require_permission_fn)]

    # ------------------------------------------------------------------
    # Read endpoints
    # ------------------------------------------------------------------

    @router.get("/freshness")
    def get_freshness(request: Request) -> dict[str, Any]:
        data = service.get_freshness()
        data["correlation_id"] = request.state.correlation_id
        return data

    @router.get("/segments")
    def list_segments(
        request: Request,
        segment_id: str | None = Query(default=None, alias="segment_id"),
    ) -> dict[str, Any]:
        items = service.list_segments(segment_id=segment_id)
        return {
            "items": items,
            "count": len(items),
            "correlation_id": request.state.correlation_id,
        }

    @router.get("/recommendations")
    def list_recommendations(
        request: Request,
        segment_id: str | None = Query(default=None, alias="segment_id"),
    ) -> dict[str, Any]:
        items = service.list_recommendations(segment_id=segment_id)
        return {
            "items": items,
            "count": len(items),
            "correlation_id": request.state.correlation_id,
        }

    @router.get("/actions")
    def list_actions(
        request: Request,
        segment_id: str | None = Query(default=None, alias="segment_id"),
        action_status: str | None = Query(default=None, alias="status"),
    ) -> dict[str, Any]:
        items = service.list_actions(segment_id=segment_id, status=action_status)
        return {
            "items": items,
            "count": len(items),
            "correlation_id": request.state.correlation_id,
        }

    @router.get("/actions/{action_id}")
    def get_action(action_id: str, request: Request) -> dict[str, Any]:
        try:
            data = service.get_action(action_id)
            data["correlation_id"] = request.state.correlation_id
            return data
        except GrowthNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/summary")
    def get_summary(request: Request) -> dict[str, Any]:
        summary = service.get_summary()
        summary["correlation_id"] = request.state.correlation_id
        return summary

    # ------------------------------------------------------------------
    # Write endpoints
    # ------------------------------------------------------------------

    @router.post("/actions", dependencies=write_deps)
    def create_action(
        body: CreateActionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return service.create_action(
                name=body.name,
                segment_id=body.segmentId,
                objective=body.objective,
                target_lift=body.targetLift,
                kind=body.kind,
                observation_window_days=body.observationWindowDays,
                observation_window=body.observationWindow,
                store=body.store,
                channel=body.channel,
                budget=body.budget,
                rationale=body.rationale,
                rollback_plan=body.rollbackPlan,
                source_recommendation_id=body.sourceRecommendationId,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        except GrowthNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except GrowthPolicyError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @router.post("/actions/{action_id}/transition", dependencies=write_deps)
    def transition_action(
        action_id: str,
        body: TransitionActionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return service.transition_action(
                action_id=action_id,
                target_status=body.targetStatus,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        except GrowthNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except GrowthCloseoutGateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except GrowthConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/actions/{action_id}/outcome", dependencies=write_deps)
    def write_outcome(
        action_id: str,
        body: OutcomePayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return service.write_outcome(
                action_id=action_id,
                outcome=body.outcome,
                required_action=body.requiredAction,
                observed_lift=body.observedLift,
                evidence_level=body.evidenceLevel,
                rationale=body.rationale,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        except GrowthNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except GrowthCloseoutGateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except GrowthConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Conflict gate + approval lifecycle (package 6 five-step builder)
    # ------------------------------------------------------------------

    @router.post("/conflicts/check")
    def check_conflicts(body: ConflictCheckPayload, request: Request) -> dict[str, Any]:
        result = service.check_conflicts(
            kind=body.kind,
            store=body.store,
            observation_window=body.observationWindow,
            channel=body.channel,
            budget=body.budget,
            exclude_action_id=body.excludeActionId,
        )
        result["correlation_id"] = request.state.correlation_id
        return result

    @router.post("/actions/{action_id}/submit", dependencies=write_deps)
    def submit_for_approval(
        action_id: str,
        body: SubmitPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return service.submit_for_approval(
                action_id=action_id,
                actor_role_id=body.actorRoleId or "growthLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        except GrowthNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except GrowthPolicyError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except GrowthConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.get("/approvals")
    def list_approvals(request: Request) -> dict[str, Any]:
        items = service.list_approvals()
        return {
            "items": items,
            "count": len(items),
            "correlation_id": request.state.correlation_id,
        }

    @router.post("/approvals/{approval_id}/decision", dependencies=write_deps)
    def decide_approval(
        approval_id: str,
        body: ApprovalDecisionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return service.resolve_approval(
                approval_id=approval_id,
                decision=body.decision,
                reason=body.reason,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        except GrowthNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except GrowthConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.get("/decisions")
    def list_decisions(request: Request) -> dict[str, Any]:
        items = service.list_decisions()
        return {
            "items": items,
            "count": len(items),
            "correlation_id": request.state.correlation_id,
        }

    return router


def _actor_name_from_role(role_id: str | None) -> str:
    return {
        "opsLead": "營運主管",
        "supportLead": "客服主管",
        "facilitiesLead": "工務主任",
        "marketingManager": "行銷經理",
        "expansionManager": "展店經理",
        "auditPm": "PM／稽核",
        "growthManager": "成長經理",
        "growthLead": "成長主管",
    }.get(role_id or "opsLead", "Operator")


__all__ = ["create_growth_sub_router"]
