"""Canonical Rebalance builder; never proxy unscoped native CRUD."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator

from models.shared_ml import ProductionExecutionConfigurationError
from modules.netplan.application import NetPlanApprovalError
from modules.opsboard.application.netplan_builder import (
    validate_builder_constraints,
    validate_native_decision,
    validate_recorded_outcome,
)
from modules.opsboard.application.network_rebalance import NetworkRebalanceRuntimeUnavailable
from shared.infrastructure.persistence.feature_flags import (
    FeatureFlagOperationDisabled,
    FeatureFlagStoreUnavailable,
)


class NetPlanBuilderCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarioId: str
    expectedProblemHash: str
    reason: str

    @model_validator(mode="before")
    @classmethod
    def safe_raw_command(cls, value):
        # Local constant-safe rejection includes missing fields and extra NaN;
        # it does not pass untrusted error inputs to the shared JSON renderer.
        if not isinstance(value, dict) or set(value) - set(cls.model_fields):
            raise HTTPException(422, "NETPLAN_BUILDER_COMMAND_INVALID")
        if (type(value.get("scenarioId")) is not str or not value["scenarioId"].strip()
                or type(value.get("reason")) is not str or not value["reason"].strip()
                or type(value.get("expectedProblemHash")) is not str
                or re.fullmatch(r"[0-9a-f]{64}", value["expectedProblemHash"]) is None):
            raise HTTPException(422, "NETPLAN_BUILDER_COMMAND_INVALID")
        if "constraints" in cls.model_fields:
            validate_builder_constraints(value.get("constraints"))
        if "outcome" in cls.model_fields:
            validate_recorded_outcome(value.get("outcome"))
        if "nativeDecision" in cls.model_fields:
            validate_native_decision(value.get("nativeDecision"))
        return value


class NetPlanRecordedOutcomeCommand(NetPlanBuilderCommand):
    outcome: dict[str, Any]


class NetPlanBuilderSaveCommand(NetPlanBuilderCommand):
    constraints: dict[str, Any]


class NetPlanBuilderDecisionCommand(NetPlanBuilderCommand):
    nativeDecision: dict[str, Any]


def create_netplan_builder_router(*, factory, require_view, require_edit, require_solve, require_decide=None):
    router = APIRouter(prefix="/network-rebalance", tags=["operator-network-rebalance"])

    def unavailable_decision_permission():
        # Existing embedders do not acquire a new approval permission by
        # omission. The real Operator mount supplies its original APPROVE guard.
        raise HTTPException(503, "NETPLAN_APPROVAL_PERMISSION_OWNER_UNAVAILABLE")

    @router.get("/stores/{store_id}/netplan/builder", dependencies=[Depends(require_view)])
    def read_builder(store_id: str, request: Request):
        try:
            return factory(request).read(store_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "NETPLAN_BUILDER_SOURCE_UNAVAILABLE") from exc

    def command(operation, store_id, body, request, idempotency_key):
        if not idempotency_key or not idempotency_key.strip():
            raise HTTPException(422, "NETPLAN_BUILDER_REASON_AND_IDEMPOTENCY_REQUIRED")
        if operation == "save" and getattr(body, "constraints", None) is None:
            raise HTTPException(422, "NETPLAN_BUILDER_CONSTRAINTS_REQUIRED")
        if operation == "solve" and getattr(body, "constraints", None) is not None:
            raise HTTPException(422, "NETPLAN_BUILDER_SAVE_BEFORE_SOLVE")
        try:
            return factory(request).command(
                store_id, operation=operation, scenario_id=body.scenarioId,
                expected_problem_hash=body.expectedProblemHash, reason=body.reason,
                constraints=getattr(body, "constraints", None), idempotency_key=idempotency_key,
                outcome=getattr(body, "outcome", None),
                **({"decision": body.nativeDecision} if isinstance(body, NetPlanBuilderDecisionCommand) else {}),
            )
        except HTTPException:
            raise
        except FeatureFlagOperationDisabled as exc:
            raise HTTPException(403, "FEATURE_FLAG_DISABLED") from exc
        except FeatureFlagStoreUnavailable as exc:
            raise HTTPException(503, "FEATURE_FLAG_STORE_UNAVAILABLE") from exc
        except (NetPlanApprovalError, ValueError) as exc:
            raise HTTPException(409, "NETPLAN_LIFECYCLE_TRANSITION_REFUSED") from exc
        except (NetworkRebalanceRuntimeUnavailable, ProductionExecutionConfigurationError) as exc:
            raise HTTPException(503, "NETPLAN_BUILDER_SOLVER_UNAVAILABLE") from exc
        except Exception as exc:
            raise HTTPException(503, "NETPLAN_BUILDER_OPERATION_UNAVAILABLE") from exc

    @router.put("/stores/{store_id}/netplan/builder", dependencies=[Depends(require_edit)])
    def save_builder(store_id: str, body: NetPlanBuilderSaveCommand, request: Request,
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        return command("save", store_id, body, request, idempotency_key)

    @router.post("/stores/{store_id}/netplan/builder/solve", dependencies=[Depends(require_solve)])
    def solve_builder(store_id: str, body: NetPlanBuilderCommand, request: Request,
                      idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        return command("solve", store_id, body, request, idempotency_key)

    @router.post("/stores/{store_id}/netplan/builder/execute", dependencies=[Depends(require_solve)])
    def execute_plan(store_id: str, body: NetPlanBuilderCommand, request: Request,
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        return command("execute", store_id, body, request, idempotency_key)

    @router.post("/stores/{store_id}/netplan/builder/decide", dependencies=[Depends(require_decide or unavailable_decision_permission)])
    def decide_plan(store_id: str, body: NetPlanBuilderDecisionCommand, request: Request,
                    idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        return command("decide", store_id, body, request, idempotency_key)

    @router.post("/stores/{store_id}/netplan/builder/outcome", dependencies=[Depends(require_solve)])
    def record_outcome(store_id: str, body: NetPlanRecordedOutcomeCommand, request: Request,
                       idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        return command("outcome", store_id, body, request, idempotency_key)

    @router.post("/stores/{store_id}/netplan/builder/close", dependencies=[Depends(require_solve)])
    def close_plan(store_id: str, body: NetPlanBuilderCommand, request: Request,
                   idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        return command("close", store_id, body, request, idempotency_key)

    return router
