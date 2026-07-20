"""One structured error envelope for every public product operation.

Before ODP-PGAP-API-001 the HTTP boundary had no error contract: 118 call
sites raised ``HTTPException(detail="some string")`` and FastAPI emitted four
mutually incompatible shapes (bare string ``detail``, a nested dict ``detail``,
Pydantic's ``[{loc,msg,type}]`` array, and in-body domain failures). A client
could not branch on an error without knowing which router produced it, and no
error body carried the correlation ID needed to find the matching audit event.

The fix is a boundary concern, not a per-route one: :func:`install_error_handlers`
registers exception handlers that normalise *every* error leaving the app --
including the 118 legacy raises -- into :class:`ErrorEnvelope`. Routes keep
raising plain ``HTTPException``; the handler supplies the envelope.

Wire shape (additive, see § Compatibility)::

    {
      "detail": "Plan P-1 cannot be approved while a rollback is pending",
      "error": {
        "code": "conflict",
        "message": "Plan P-1 cannot be approved while a rollback is pending",
        "next_action": "Resolve the conflicting state and retry.",
        "occurred_at": "2026-07-15T09:12:44.512Z",
        "details": [],
        "correlation_id": "corr-abc123"
      }
    }

Compatibility
-------------
``detail`` is retained deliberately and is not deprecated here. The operator
console surfaces the server's zh-TW refusal copy by reading ``OdpApiError.detail``
(``packages/openapi-client/src/index.ts``), and 15 backend tests assert on
``response.json()["detail"]``. Dropping it would silently blank every policy
refusal in the UI. ``error`` is therefore added *alongside* ``detail``, and
``error.message`` always carries the same text ``detail`` does.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

__all__ = [
    "ErrorCode",
    "ErrorEnvelope",
    "ApiError",
    "error_response_body",
    "install_error_handlers",
    "ERROR_RESPONSES",
]


class ErrorCode:
    """Stable, machine-branchable error codes.

    Codes are part of the public contract: clients branch on them, so they are
    snake_case constants rather than an ``Enum`` whose ``.value`` callers would
    have to remember to unwrap when building an envelope by hand.
    """

    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNPROCESSABLE = "unprocessable_entity"
    VALIDATION_FAILED = "validation_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal_error"


# Status -> (code, next_action). ``next_action`` tells the caller what to *do*,
# which is the field that makes an envelope actionable rather than decorative.
_STATUS_MAP: dict[int, tuple[str, str]] = {
    400: (ErrorCode.BAD_REQUEST, "Correct the request and retry."),
    401: (ErrorCode.UNAUTHORIZED, "Authenticate and retry with a valid identity."),
    403: (ErrorCode.FORBIDDEN, "Request access for this role or tenant; do not retry as-is."),
    404: (ErrorCode.NOT_FOUND, "Verify the identifier; the resource may have been removed."),
    409: (ErrorCode.CONFLICT, "Resolve the conflicting state and retry."),
    412: (ErrorCode.CONFLICT, "Re-read the resource and retry with current preconditions."),
    422: (ErrorCode.UNPROCESSABLE, "Fix the highlighted fields and resubmit."),
    429: (ErrorCode.RATE_LIMITED, "Back off and retry after the indicated interval."),
    500: (ErrorCode.INTERNAL, "Retry later; if it persists, escalate with the correlation ID."),
    502: (ErrorCode.INTERNAL, "Upstream failed; retry later with the correlation ID."),
    503: (ErrorCode.INTERNAL, "Service unavailable; retry after a short back-off."),
}

_FALLBACK = (ErrorCode.INTERNAL, "Retry later; if it persists, escalate with the correlation ID.")

logger = logging.getLogger("oday-api.errors")


class ErrorEnvelope(BaseModel):
    """The single error contract for every endpoint."""

    code: str = Field(description="Stable machine-readable code; clients branch on this.")
    message: str = Field(description="Human-readable summary; safe to display to an operator.")
    next_action: str = Field(description="What the caller should do next.")
    occurred_at: str = Field(description="RFC3339 UTC timestamp of when the error was produced.")
    details: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-field or per-cause breakdown; empty when the error is not field-scoped.",
    )
    correlation_id: str | None = Field(
        default=None,
        description="Correlation ID; matches the X-Correlation-Id response header and audit log.",
    )


class ErrorResponse(BaseModel):
    """Full error body: the structured envelope plus the legacy ``detail``.

    Declared as a model so it lands in the OpenAPI artifact and therefore in the
    generated TypeScript client, rather than being an undocumented dict.
    """

    detail: Any = Field(
        description=(
            "Legacy detail, passed through exactly as the route produced it: a string, "
            "Pydantic's [{loc,msg,type}] array, or a route-specific object. Retained "
            "unchanged for existing consumers. New clients should read `error` instead."
        )
    )
    error: ErrorEnvelope


class ApiError(Exception):
    """Raise to return a fully-specified envelope.

    Prefer this over ``HTTPException`` in new code when a specific ``code`` or
    ``next_action`` matters. Legacy ``HTTPException`` raises still produce a
    valid envelope via the installed handler -- they just fall back to the
    status-derived code and next action.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        next_action: str | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        default_code, default_action = _STATUS_MAP.get(status_code, _FALLBACK)
        self.status_code = status_code
        self.message = message
        self.code = code or default_code
        self.next_action = next_action or default_action
        self.details = details or []


# Sentinel: `None` is a legitimate `detail`, so it cannot mark "not supplied".
_UNSET: Any = object()


def _now() -> str:
    # Millisecond precision + trailing "Z": RFC3339 as JS Date.parse expects it.
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _summarize_detail(detail: Any) -> tuple[str, list[dict[str, Any]]]:
    """Derive (message, details) for the envelope from any legacy ``detail``.

    This only *reads* ``detail`` to populate the new envelope; it never rewrites
    what goes on the wire. Routes emit three shapes and each is summarised
    without being flattened away:

    * ``str`` -- already the message.
    * ``[{loc,msg,type}]`` -- Pydantic validation errors, joined the same way
      ``flattenApiDetail`` in the TypeScript client joins them, so the message a
      client computes locally and the one the server sends agree.
    * ``dict`` -- a route-specific object (``network_scoring`` sends
      ``{message, missing}``; ``network_rebalance`` sends a ``state`` flag).
      Its ``message`` is lifted out and the whole object preserved in
      ``details`` so nothing is lost.
    """
    if isinstance(detail, str):
        return detail, []
    if isinstance(detail, list):
        parts: list[str] = []
        rows: list[dict[str, Any]] = []
        for issue in detail:
            if not isinstance(issue, dict):
                parts.append(str(issue))
                continue
            loc = [str(x) for x in issue.get("loc", []) if x != "body"]
            field = ".".join(loc)
            msg = str(issue.get("msg", ""))
            rows.append({"field": field, "message": msg, "type": str(issue.get("type", ""))})
            parts.append(f"{field}: {msg}" if field and msg else msg)
        return "; ".join(p for p in parts if p), rows
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("detail") or "Request failed")
        return message, [detail]
    return str(detail), []


def error_response_body(
    *,
    code: str,
    message: str,
    next_action: str,
    correlation_id: str | None,
    details: list[dict[str, Any]] | None = None,
    detail: Any = _UNSET,
) -> dict[str, Any]:
    """Build the wire body. Single place that decides the JSON shape.

    ``detail`` is passed through **verbatim** -- string, list or object -- and is
    only defaulted to ``message`` when a caller supplies none. Summarising it
    into a string here would be a silent breaking change: the operator console
    reads ``detail`` for refusal copy, and two endpoints return objects whose
    fields callers branch on (``network_rebalance``'s ``state`` retry flag,
    ``network_scoring``'s ``missing`` list). The envelope is additive by
    construction; ``detail`` means today exactly what it meant before.
    """
    envelope = ErrorEnvelope(
        code=code,
        message=message,
        next_action=next_action,
        occurred_at=_now(),
        details=details or [],
        correlation_id=correlation_id,
    )
    return {
        "detail": message if detail is _UNSET else detail,
        "error": envelope.model_dump(),
    }


# Reusable OpenAPI `responses=` fragment so error bodies are documented on
# endpoints rather than inferred. Used by the router mount helper.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid request"},
    401: {"model": ErrorResponse, "description": "Missing or invalid identity"},
    403: {"model": ErrorResponse, "description": "Server-derived authorization denied"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {"model": ErrorResponse, "description": "Conflicting state or idempotency replay conflict"},
    422: {"model": ErrorResponse, "description": "Validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected server error"},
}


def install_error_handlers(app: Any) -> None:
    """Register handlers that normalise every error leaving ``app``.

    Covers the three ways an error can surface today:
    ``ApiError`` (new code), ``HTTPException`` (the 118 legacy raises, plus
    FastAPI's own 404/405), and ``RequestValidationError`` (Pydantic 422).
    """
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException
    from starlette.requests import Request

    # Registered against Starlette's HTTPException, not FastAPI's. Starlette
    # resolves handlers by walking the exception's MRO, and FastAPI's
    # HTTPException subclasses Starlette's -- so this one registration covers
    # both, including the 404/405 Starlette itself raises during routing.

    def _correlation_id(request: Request) -> str | None:
        return getattr(request.state, "correlation_id", None)

    def _is_v1(request: Request) -> bool:
        path = request.url.path
        intake_prefixes = (
            "/api/v1/intakes",
            "/api/v1/intake-batches",
            "/api/v1/jobs",
            "/api/v1/saved-views",
            "/api/v1/match-cases",
            "/api/v1/identity/",
            "/api/v1/promotion-decisions",
            "/api/v1/assignments",
            "/api/v1/sla-instances",
            "/api/v1/identity-decisions",
            "/v1/intakes",
            "/v1/intake-batches",
            "/v1/saved-views",
            "/v1/match-cases",
            "/v1/identity/",
            "/v1/promotion-decisions",
            "/v1/assignments",
            "/v1/sla-instances",
            "/v1/identity-decisions",
        )
        if path.startswith(intake_prefixes):
            if path.startswith(("/api/v1/jobs", "/v1/jobs")) and not path.endswith("/retry"):
                return False
            return True
        return False


    def map_to_api_error(status_code: int, detail: Any, correlation_id: str | None) -> dict[str, Any]:
        code = "INTERNAL_ERROR"
        next_action = None
        retryable = False
        field_errors = []

        detail_str = ""
        if isinstance(detail, str):
            detail_str = detail
        elif isinstance(detail, list):
            parts = []
            for issue in detail:
                if isinstance(issue, dict):
                    loc = [str(x) for x in issue.get("loc", []) if x != "body"]
                    field = ".".join(loc)
                    msg = str(issue.get("msg", ""))
                    code_str = str(issue.get("type", ""))
                    field_errors.append({
                        "field": field or "body",
                        "code": "FIELD_REQUIRED" if "missing" in code_str else "VALIDATION_FAILED",
                        "message": msg
                    })
                    parts.append(f"{field}: {msg}" if field and msg else msg)
            detail_str = "; ".join(p for p in parts if p) or "Validation failed"
        elif isinstance(detail, dict):
            detail_str = str(detail.get("message") or detail.get("detail") or "Request failed")

        if "AUTHENTICATION_REQUIRED" in detail_str or "principal not authenticated" in detail_str or status_code == 401:
            code = "AUTHENTICATION_REQUIRED"
            next_action = "CONTACT_SUPPORT"
        elif "ROLE_DENIED" in detail_str or status_code == 403:
            code = "ROLE_DENIED"
            next_action = "REQUEST_ACCESS"
            if "TENANT_SCOPE_DENIED" in detail_str:
                code = "TENANT_SCOPE_DENIED"
            elif "SCOPE_DENIED" in detail_str:
                code = "SCOPE_DENIED"
            elif "OWNERSHIP_REQUIRED" in detail_str:
                code = "OWNERSHIP_REQUIRED"
            elif "ASSIGNMENT_SCOPE_DENIED" in detail_str:
                code = "ASSIGNMENT_SCOPE_DENIED"
            elif "SELF_REVIEW_DENIED" in detail_str:
                code = "SELF_REVIEW_DENIED"
        elif "RISK_ACKNOWLEDGEMENT_REQUIRED" in detail_str or "risk summary is required" in detail_str or "risk acknowledgement is required" in detail_str:
            code = "RISK_ACKNOWLEDGEMENT_REQUIRED"
            next_action = "CORRECT_INPUT"
        elif "LEGAL_HOLD_CONFLICT" in detail_str:
            code = "LEGAL_HOLD_CONFLICT"
            next_action = "CONTACT_SUPPORT"
        elif "WORKFLOW_STATE_DENIED" in detail_str:
            code = "WORKFLOW_STATE_DENIED"
            next_action = "REFRESH"
        elif "VERSION_CONFLICT" in detail_str or "version conflict" in detail_str:
            code = "VERSION_CONFLICT"
            next_action = "REFRESH"
        elif "SECOND_ACTOR_REQUIRED" in detail_str:
            code = "SECOND_ACTOR_REQUIRED"
            next_action = "CONTACT_SUPPORT"
        elif "idempotency key was used with another payload" in detail_str:
            code = "IDEMPOTENCY_KEY_REUSED"
            next_action = "CORRECT_INPUT"
        elif "Idempotency-Key is required" in detail_str:
            code = "VALIDATION_FAILED"
            next_action = "CORRECT_INPUT"
        elif "If-Match is required" in detail_str or "If-Match header" in detail_str:
            code = "PRECONDITION_REQUIRED"
            next_action = "CORRECT_INPUT"
        elif "not found" in detail_str or status_code == 404:
            code = "RESOURCE_NOT_FOUND"
            next_action = "REFRESH"
        elif "cursor" in detail_str:
            code = "CURSOR_INVALID"
            next_action = "REFRESH"
        elif status_code == 422:
            code = "VALIDATION_FAILED"
            next_action = "CORRECT_INPUT"
        elif status_code == 409:
            code = "VERSION_CONFLICT"
            next_action = "REFRESH"
        elif status_code == 503:
            code = "BACKPRESSURE_ACTIVE"
            next_action = "WAIT"
            retryable = True
        elif status_code == 400:
            code = "VALIDATION_FAILED"
            next_action = "CORRECT_INPUT"

        try:
            contract_correlation_id = str(UUID(correlation_id)) if correlation_id else str(uuid4())
        except (TypeError, ValueError):
            contract_correlation_id = str(uuid4())

        occurred_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return {
            "code": code,
            "message": detail_str,
            "retryable": retryable,
            "correlation_id": contract_correlation_id,
            "reason_code": None,
            "field_errors": field_errors or None,
            "current_version": None,
            "retry_after_seconds": 30 if code == "BACKPRESSURE_ACTIVE" else None,
            "occurred_at": occurred_at,
            "next_action": next_action
        }

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        if _is_v1(request):
            return JSONResponse(
                status_code=exc.status_code,
                content=map_to_api_error(exc.status_code, exc.message, _correlation_id(request)),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response_body(
                code=exc.code,
                message=exc.message,
                next_action=exc.next_action,
                correlation_id=_correlation_id(request),
                details=exc.details,
            ),
        )

    @app.exception_handler(HTTPException)
    async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        if _is_v1(request):
            return JSONResponse(
                status_code=exc.status_code,
                content=map_to_api_error(exc.status_code, exc.detail, _correlation_id(request)),
                headers=getattr(exc, "headers", None),
            )
        code, next_action = _STATUS_MAP.get(exc.status_code, _FALLBACK)
        message, details = _summarize_detail(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response_body(
                code=code,
                message=message,
                next_action=next_action,
                correlation_id=_correlation_id(request),
                details=details,
                detail=exc.detail,
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = _correlation_id(request)
        logger.exception(
            "Unhandled exception on %s %s (correlation_id=%s)",
            request.method,
            request.url.path,
            correlation_id,
        )
        if _is_v1(request):
            return JSONResponse(
                status_code=500,
                content=map_to_api_error(500, "Internal server error", correlation_id),
            )
        return JSONResponse(
            status_code=500,
            content=error_response_body(
                code=ErrorCode.INTERNAL,
                message="Internal server error",
                next_action=_FALLBACK[1],
                correlation_id=correlation_id,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        from fastapi.encoders import jsonable_encoder
        encoded = jsonable_encoder(exc.errors())

        # Missing If-Match is a 428 precondition failure; a present but
        # malformed weak ETag is a 400 request-format failure.
        for error in encoded:
            loc = error.get("loc", [])
            if len(loc) >= 2 and str(loc[0]) == "header" and str(loc[1]).lower() == "if-match":
                if _is_v1(request):
                    if error.get("type") != "missing":
                        return JSONResponse(
                            status_code=400,
                            content=map_to_api_error(
                                400,
                                "invalid If-Match format; expected W/\"<version>\"",
                                _correlation_id(request),
                            ),
                        )
                    return JSONResponse(
                        status_code=428,
                        content=map_to_api_error(428, "Precondition Required: If-Match header is required", _correlation_id(request)),
                    )
                else:
                    return JSONResponse(
                        status_code=428,
                        content=error_response_body(
                            code=ErrorCode.VALIDATION_FAILED,
                            message="Precondition Required: If-Match header is required",
                            next_action="Provide the If-Match header.",
                            correlation_id=_correlation_id(request),
                        )
                    )

        if _is_v1(request):
            return JSONResponse(
                status_code=422,
                content=map_to_api_error(422, encoded, _correlation_id(request)),
            )
        message, details = _summarize_detail(encoded)
        return JSONResponse(
            status_code=422,
            content=error_response_body(
                code=ErrorCode.VALIDATION_FAILED,
                message=message or "Request validation failed",
                next_action="Fix the highlighted fields and resubmit.",
                correlation_id=_correlation_id(request),
                details=details,
                detail=encoded,
            ),
        )
