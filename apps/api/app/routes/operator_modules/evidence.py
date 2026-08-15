"""Operator Console evidence sub-router.

Owns: POST /operator/evidence/{evidence_id}/purpose
Not touching: issues, approvals, seed, shell sub-routers
Composes with: create_operator_router() in operator.py — caller must pass
    require_permission_fn so the purpose write endpoint carries the correct auth guard.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header

from modules.opsboard.application.operator_state import OperatorStateService
from modules.opsboard.domain.r4_dtos import EvidencePurposeRequest, EvidencePurposeResponse


def create_evidence_sub_router(
    state_service: OperatorStateService,
    require_permission_fn: Callable[..., Any],
) -> APIRouter:
    """Return the evidence sub-router.

    Parameters
    ----------
    state_service:
        Shared OperatorStateService instance.
    require_permission_fn:
        A callable that returns a FastAPI dependency for the write guard.
        Typically ``require_permission("intervention", Action.CREATE, engine=...)``.
    """
    router = APIRouter()

    @router.post(
        "/evidence/{evidence_id}/purpose",
        dependencies=[Depends(require_permission_fn)],
    )
    def confirm_evidence_purpose(
        evidence_id: str,
        body: EvidencePurposeRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> EvidencePurposeResponse:
        """Unlock a locked evidence item by declaring its access purpose.

        Caller must supply purpose, privacyAcknowledged=True for camera evidence,
        and retentionHours within the policy ceiling.
        Idempotency-Key header de-duplicates in-flight retries.
        """
        result = state_service.confirm_evidence_purpose(
            evidence_id=evidence_id,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=x_correlation_id,
        )
        return result

    return router


__all__ = ["create_evidence_sub_router"]
