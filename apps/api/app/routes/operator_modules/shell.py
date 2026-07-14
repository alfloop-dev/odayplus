from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel

from modules.opsboard.application.operator_shell import OperatorShellService
from shared.audit import InMemoryAuditLog


class TransitionPayload(BaseModel):
    issueId: str | None = None
    status: str | None = None
    note: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None


class ApprovalDecisionPayload(BaseModel):
    status: str
    reason: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None


class EvidencePurposePayload(BaseModel):
    purpose: str
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = None
    privacyAcknowledged: bool | None = None
    auditNote: str | None = None
    actorName: str | None = None


def _context(
    *,
    x_operator_role: str | None,
    x_subject_id: str | None,
    x_roles: str | None,
    x_correlation_id: str | None,
) -> dict[str, str | None]:
    return {
        "role_id": x_operator_role,
        "subject_id": x_subject_id,
        "system_roles": x_roles,
        "correlation_id": x_correlation_id,
    }


def create_operator_router(
    *,
    audit_log: InMemoryAuditLog | None = None,
    service: OperatorShellService | None = None,
) -> APIRouter:
    from apps.api.oday_api.security.dependencies import build_engine, require_permission
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)
    idempotency_cache: dict[str, Any] = {}
    shell = service or OperatorShellService()

    router = APIRouter(prefix="/operator", tags=["operator"])

    @router.get("/bootstrap")
    def bootstrap(
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        return shell.bootstrap(
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    @router.get("/today")
    def today(
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        return shell.bootstrap(
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )

    @router.get("/search")
    def search(
        q: str = Query(default=""),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        return shell.search(
            q,
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )

    @router.get("/issues")
    def get_issues(
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        envelope = shell.bootstrap(
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )
        return {
            "items": envelope["workQueue"],
            "count": len(envelope["workQueue"]),
            "meta": envelope["meta"],
        }

    @router.get("/approvals")
    def get_approvals(
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        envelope = shell.bootstrap(
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            )
        )
        return {
            "items": envelope["approvals"],
            "count": len(envelope["approvals"]),
            "meta": envelope["meta"],
        }

    @router.post(
        "/issues/{issue_id}/{action_type}",
        dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))],
    )
    def transition_issue(
        issue_id: str,
        action_type: str,
        body: TransitionPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        if idempotency_key and idempotency_key in idempotency_cache:
            return idempotency_cache[idempotency_key]

        envelope = shell.transition_issue(
            body.issueId or issue_id,
            action_type=action_type,
            note=body.note,
            actor_name=body.actorName,
            **_context(
                x_operator_role=x_operator_role or body.actorRoleId,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )
        if idempotency_key:
            idempotency_cache[idempotency_key] = envelope
        return envelope

    @router.post(
        "/approvals/{approval_id}/decision",
        dependencies=[Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))],
    )
    def decide_approval(
        approval_id: str,
        body: ApprovalDecisionPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        if idempotency_key and idempotency_key in idempotency_cache:
            return idempotency_cache[idempotency_key]

        envelope = shell.decide_approval(
            approval_id,
            status=body.status,
            reason=body.reason,
            actor_name=body.actorName,
            **_context(
                x_operator_role=x_operator_role or body.actorRoleId,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )
        if idempotency_key:
            idempotency_cache[idempotency_key] = envelope
        return envelope

    @router.post(
        "/evidence/{evidence_id}/purpose",
        dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))],
    )
    def confirm_evidence_purpose(
        evidence_id: str,
        body: EvidencePurposePayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_operator_role: str | None = Header(default=None, alias="X-Operator-Role"),
        x_subject_id: str | None = Header(default=None, alias="X-Subject-Id"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        if idempotency_key and idempotency_key in idempotency_cache:
            return idempotency_cache[idempotency_key]

        envelope = shell.confirm_evidence_purpose(
            evidence_id,
            purpose=body.purpose,
            actor_name=body.actorName,
            **_context(
                x_operator_role=x_operator_role,
                x_subject_id=x_subject_id,
                x_roles=x_roles,
                x_correlation_id=x_correlation_id,
            ),
        )
        if idempotency_key:
            idempotency_cache[idempotency_key] = envelope
        return envelope

    return router


__all__ = ["create_operator_router"]
