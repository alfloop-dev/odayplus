"""Operator Console seed sub-router.

Owns: POST /operator/seed/reset
Not touching: shell, issues, approvals, evidence sub-routers
Composes with: create_operator_router() in operator.py

Purpose: deterministic seed reset for tests and dev environments.
The seed payload is loaded from tests/fixtures/operator_console/seed_r4.json
(or modules/opsboard/infrastructure/seed_data.py as fallback).
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header

from modules.opsboard.application.operator_state import OperatorStateService


def create_seed_sub_router(
    state_service: OperatorStateService,
    *,
    require_reset_permission_fn: Callable[..., Any],
) -> APIRouter:
    """Return the seed sub-router."""
    router = APIRouter()

    @router.post("/seed/reset", dependencies=[Depends(require_reset_permission_fn)])
    def reset_seed(
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        """Reset the in-memory operator state to the canonical R4 seed.

        Idempotent: repeated calls return the same deterministic state.
        Used by integration tests and dev-environment setup scripts.
        """
        state_service.reset_to_seed()
        return {
            "status": "ok",
            "message": "Operator state reset to canonical R4 seed.",
            "correlation_id": x_correlation_id,
        }

    return router


__all__ = ["create_seed_sub_router"]
