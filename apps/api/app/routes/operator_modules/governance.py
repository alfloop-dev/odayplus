"""Govern workspace FastAPI sub-router (ODP-OC-R4-009).

Routes (all under /operator/governance):
  GET  /snapshot            — full Govern snapshot (approvals, decisions, audit,
                              status board, evidence-package history)
  GET  /evidence-packages   — evidence-package export history
  POST /decisions           — approve / return / reject an approval; the
                              return/reject-requires-reason policy is enforced
                              server-side (422 on violation)
  POST /evidence-package     — export an Evidence Package, recording scope,
                              range, format, actor, correlation, retention

Auth: write endpoints require the intervention guards passed from operator.py
(decisions → APPROVE, export → CREATE); read endpoints are open (oversight
surface).  Idempotency-Key is handled by the service; X-Correlation-Id is read
from request.state.correlation_id.

Composes with: create_operator_router() in operator.py.  Owned layer: the
Govern workspace API only — it does not redefine approvals/evidence routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from modules.opsboard.application.governance import (
    DECISION_ACTIONS,
    GovernanceConflict,
    GovernanceNotFound,
    GovernancePolicyError,
    GovernanceService,
)

# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------


class DecisionPayload(BaseModel):
    """POST /operator/governance/decisions — approve / return / reject."""

    model_config = ConfigDict(extra="allow")

    approvalId: str
    action: str
    reason: str = ""
    role: str = "營運主管"
    actorName: str | None = None

    @field_validator("approvalId")
    @classmethod
    def non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("approvalId must not be empty")
        return v

    @field_validator("action")
    @classmethod
    def valid_action(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in DECISION_ACTIONS:
            raise ValueError(f"action must be one of {', '.join(DECISION_ACTIONS)}")
        return v


class EvidencePackagePayload(BaseModel):
    """POST /operator/governance/evidence-package — export an Evidence Package."""

    model_config = ConfigDict(extra="allow")

    dateFrom: str
    dateTo: str
    modules: list[str] = Field(default_factory=list)
    contents: list[str] = Field(default_factory=list)
    format: str = "PDF"
    role: str = "營運主管"
    actorName: str | None = None
    retentionPolicy: str | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_governance_sub_router(
    service: GovernanceService,
    *,
    require_view_permission_fn: Any = None,
    require_decision_permission_fn: Any = None,
    require_export_permission_fn: Any = None,
) -> APIRouter:
    """Return the Govern sub-router wired to a shared GovernanceService."""
    router = APIRouter(prefix="/governance", tags=["operator-governance"])

    read_deps: list[Any] = (
        [Depends(require_view_permission_fn)] if require_view_permission_fn else []
    )
    decision_deps: list[Any] = (
        [Depends(require_decision_permission_fn)] if require_decision_permission_fn else []
    )
    export_deps: list[Any] = (
        [Depends(require_export_permission_fn)] if require_export_permission_fn else []
    )

    # ------------------------------------------------------------------
    # Read endpoints
    # ------------------------------------------------------------------

    @router.get("/snapshot", dependencies=read_deps)
    def get_snapshot(
        request: Request,
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
    ) -> dict[str, Any]:
        return service.snapshot(
            role_id=getattr(request.state, "operator_role_id", None) or x_operator_role,
            correlation_id=request.state.correlation_id,
        )

    @router.get("/evidence-packages", dependencies=read_deps)
    def list_evidence_packages(request: Request) -> dict[str, Any]:
        snap = service.snapshot(correlation_id=request.state.correlation_id)
        items = snap["evidencePackages"]
        return {
            "items": items,
            "count": len(items),
            "correlation_id": request.state.correlation_id,
        }

    # ------------------------------------------------------------------
    # Write endpoints
    # ------------------------------------------------------------------

    @router.post("/decisions", dependencies=decision_deps)
    def decide(
        body: DecisionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return service.decide(
                approval_id=body.approvalId,
                action=body.action,
                reason=body.reason,
                role=body.role,
                actor_name=body.actorName,
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        except GovernancePolicyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        except GovernanceNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except GovernanceConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/evidence-package", dependencies=export_deps)
    def export_evidence_package(
        body: EvidencePackagePayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return service.export_evidence_package(
            date_from=body.dateFrom,
            date_to=body.dateTo,
            modules=body.modules,
            contents=body.contents,
            fmt=body.format,
            role=body.role,
            actor_name=body.actorName,
            retention_policy=body.retentionPolicy,
            idempotency_key=idempotency_key,
            correlation_id=request.state.correlation_id,
        )

    return router


__all__ = ["create_governance_sub_router"]
