"""Operator Console shell sub-router.

Owns: /operator/bootstrap, /operator/today
Not touching: issues, approvals, evidence, seed sub-routers
Composes with: create_operator_router() in operator.py
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from modules.opsboard.application.operator_state import OperatorStateService


def create_shell_sub_router(state_service: OperatorStateService) -> APIRouter:
    """Return the shell sub-router (bootstrap + today endpoints)."""
    router = APIRouter()

    @router.get("/bootstrap")
    def bootstrap() -> dict[str, Any]:
        """Return the full operator console bootstrap payload."""
        return state_service.get_today()

    @router.get("/today")
    def get_today() -> dict[str, Any]:
        """Return today's operational snapshot (alias of bootstrap for FE compat)."""
        return state_service.get_today()

    return router


__all__ = ["create_shell_sub_router"]
