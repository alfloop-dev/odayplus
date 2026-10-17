"""Scoped AVM finance/DataRoom routes; actual actor never comes from a body."""
from __future__ import annotations

import math
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator


class AVMCreateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valuationInput: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def safe_input(cls, value):
        from apps.api.app.routes.avm import AVMCasePayload

        def finite_json(item):
            if isinstance(item, float):
                return math.isfinite(item)
            if isinstance(item, dict):
                return all(finite_json(v) for v in item.values())
            if isinstance(item, list):
                return all(finite_json(v) for v in item)
            return True

        if not isinstance(value, dict) or set(value) != {"valuationInput"} or not isinstance(value["valuationInput"], dict) or not finite_json(value):
            raise HTTPException(422, "AVM_VALUATION_INPUT_INVALID")
        data = value["valuationInput"]
        allowed = set(AVMCasePayload.model_fields) - {"created_by", "idempotency_key"}
        if set(data) - allowed:
            raise HTTPException(422, "AVM_CALLER_ACTOR_OR_UNKNOWN_FIELD_FORBIDDEN")
        try:
            parsed = AVMCasePayload.model_validate({**data, "created_by": "resolved-by-server"})
        except ValidationError as exc:
            raise HTTPException(422, "AVM_VALUATION_INPUT_INVALID") from exc
        return {"valuationInput": parsed.model_dump(mode="json", exclude={"created_by", "idempotency_key"})}


class AVMCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: str
    reason: str
    reservePrice: float | None = None

    @model_validator(mode="before")
    @classmethod
    def safe_raw(cls, value):
        if (not isinstance(value, dict) or set(value) - set(cls.model_fields)
                or type(value.get("expectedVersion")) is not str
                or re.fullmatch(r"[a-f0-9]{64}", value["expectedVersion"]) is None
                or type(value.get("reason")) is not str or not value["reason"].strip()):
            raise HTTPException(422, "AVM_COMMAND_INVALID")
        reserve = value.get("reservePrice")
        if reserve is not None:
            try:
                valid = type(reserve) in (int, float) and math.isfinite(reserve)
            except OverflowError:
                valid = False
            if not valid:
                raise HTTPException(422, "AVM_COMMAND_INVALID")
        return value


def create_avm_dealroom_router(*, factory, require_view, require_create, require_approve, require_export):
    router = APIRouter(prefix="/avm", tags=["operator-avm-dealroom"])

    def actual_actor(request, expected):
        principal = request.state.operator_principal
        if not expected or expected != principal.subject_id:
            raise HTTPException(403, "AVM_EXPECTED_ACTOR_MISMATCH")

    @router.get("/context", dependencies=[Depends(require_view)])
    def context(request: Request):
        return factory(request).context()

    @router.get("/cases", dependencies=[Depends(require_view)])
    def cases(request: Request):
        return factory(request).list_cases()

    @router.post("/cases", status_code=201, dependencies=[Depends(require_create)])
    def create(body: AVMCreateCommand, request: Request,
               idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
               expected_actor: str | None = Header(None, alias="If-Actor-Match")):
        actual_actor(request, expected_actor)
        if not idempotency_key or not idempotency_key.strip():
            raise HTTPException(422, "AVM_IDEMPOTENCY_REQUIRED")
        return factory(request).create_case(body.valuationInput, idempotency_key=idempotency_key)

    @router.get("/cases/{case_id}", dependencies=[Depends(require_view)])
    def detail(case_id: str, request: Request):
        return factory(request).read(case_id)

    def command(case_id, operation, body, request, key, expected_actor):
        actual_actor(request, expected_actor)
        if not key or not key.strip():
            raise HTTPException(422, "AVM_IDEMPOTENCY_REQUIRED")
        if operation != "approve" and body.reservePrice is not None:
            raise HTTPException(422, "AVM_RESERVE_OVERRIDE_ONLY_FOR_FINANCE_APPROVAL")
        return factory(request).command(case_id, operation=operation,
            expected_version=body.expectedVersion, reason=body.reason,
            reserve_price=body.reservePrice, idempotency_key=key)

    @router.post("/cases/{case_id}/finance-approval", dependencies=[Depends(require_approve)])
    def approve(case_id: str, body: AVMCommand, request: Request,
                idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                expected_actor: str | None = Header(None, alias="If-Actor-Match")):
        return command(case_id, "approve", body, request, idempotency_key, expected_actor)

    @router.post("/cases/{case_id}/normalize", dependencies=[Depends(require_create)])
    def normalize(case_id: str, body: AVMCommand, request: Request,
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                  expected_actor: str | None = Header(None, alias="If-Actor-Match")):
        return command(case_id, "normalize", body, request, idempotency_key, expected_actor)

    @router.post("/cases/{case_id}/value", dependencies=[Depends(require_create)])
    def value(case_id: str, body: AVMCommand, request: Request,
              idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
              expected_actor: str | None = Header(None, alias="If-Actor-Match")):
        return command(case_id, "value", body, request, idempotency_key, expected_actor)

    @router.post("/cases/{case_id}/dataroom", dependencies=[Depends(require_create)])
    def build(case_id: str, body: AVMCommand, request: Request,
              idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
              expected_actor: str | None = Header(None, alias="If-Actor-Match")):
        return command(case_id, "build", body, request, idempotency_key, expected_actor)

    @router.post("/cases/{case_id}/dataroom/exports", dependencies=[Depends(require_export)])
    def export(case_id: str, body: AVMCommand, request: Request,
               idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
               expected_actor: str | None = Header(None, alias="If-Actor-Match")):
        return command(case_id, "export", body, request, idempotency_key, expected_actor)

    @router.get("/cases/{case_id}/dataroom/exports/{export_id}", dependencies=[Depends(require_export)])
    def download(case_id: str, export_id: str, request: Request,
                 expected_actor: str | None = Header(None, alias="If-Actor-Match")):
        actual_actor(request, expected_actor)
        row = factory(request).export_content(case_id, export_id)
        return Response(content=row["content"], media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"',
                     "X-Content-SHA256": row["sha256"], "X-AVM-Export-Id": row["exportId"],
                     "Cache-Control": "private, no-store"})

    return router
