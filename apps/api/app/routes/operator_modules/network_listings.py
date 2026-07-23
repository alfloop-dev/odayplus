"""Operator Console Network Listing Radar routes.

Owns:
- GET /operator/network-listings
- POST /operator/network-listings/listings/{id}/convert
- POST /operator/network-listings/listings/{id}/merge
- POST /operator/network-listings/listings/{id}/archive

The routes wrap NetworkListingService and keep write auth/idempotency headers at
the HTTP boundary. They compose through apps.api.app.routes.operator.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from modules.external_data.security import contains_sensitive_submission_material
from modules.listing.application.intake_authorization import (
    authorize_intake_action,
    mask_intake,
    mask_listing,
)
from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
    NetworkListingNotFound,
    NetworkListingPolicyError,
    NetworkListingService,
)
from shared.audit import InMemoryAuditLog
from shared.auth import Principal, Role

_INTAKE_CURSOR_TTL = timedelta(hours=1)
_INTAKE_SAVED_VIEWS = {
    "all",
    "needsReview",
    "awaitingEntry",
    "blocked",
    "processing",
    "ready",
}
_INTAKE_SORT_FIELDS = {
    "id",
    "sourceId",
    "intakeMethod",
    "stage",
    "matchOutcome",
    "owner",
    "submitter",
    "assignmentStatus",
    "slaState",
    "dueAt",
    "observedAt",
    "updatedAt",
}


def _latest_intake_timestamp(item: dict[str, Any]) -> str:
    events = item.get("auditEvents") or []
    return (
        (events[-1].get("occurredAt") if events else None)
        or item.get("updatedAt")
        or item.get("capturedAt")
        or ""
    )


def _intake_effective_field(item: dict[str, Any], *keys: str) -> Any:
    fields = item.get("parsedFields") or {}
    for key in keys:
        cell = fields.get(key)
        if not isinstance(cell, dict) or cell.get("masked"):
            continue
        for value_key in ("correctedValue", "normalizedValue", "sourceValue"):
            value = cell.get(value_key)
            if value is not None and value != "":
                return value
    return None


def _intake_location(item: dict[str, Any]) -> dict[str, Any] | None:
    latitude = _intake_effective_field(item, "latitude", "lat")
    longitude = _intake_effective_field(item, "longitude", "lng", "lon")
    if latitude is None or longitude is None:
        raw_snapshot = item.get("rawSnapshot")
        if isinstance(raw_snapshot, dict):
            latitude = latitude if latitude is not None else raw_snapshot.get("latitude", raw_snapshot.get("lat"))
            longitude = longitude if longitude is not None else raw_snapshot.get(
                "longitude", raw_snapshot.get("lng", raw_snapshot.get("lon"))
            )
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude_value <= 90 and -180 <= longitude_value <= 180):
        return None
    return {
        "latitude": latitude_value,
        "longitude": longitude_value,
        "confidence": _intake_effective_field(
            item, "geocodeConfidence", "geocode_confidence"
        ),
        "source": "parsed-field-or-source-snapshot",
    }


def _decorate_intake_for_inbox(item: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(item)
    parsed_fields = decorated.get("parsedFields") or {}
    masked_fields = [
        key
        for key, cell in parsed_fields.items()
        if isinstance(cell, dict) and cell.get("masked")
    ]
    match_result = decorated.get("matchResult") or {}
    scope = decorated.get("scope") or {}
    failure = decorated.get("failure") or {}
    decorated.update(
        {
            "assignedAreaId": decorated.get("assignedAreaId")
            or scope.get("assigned_area_id"),
            "brandId": decorated.get("brandId") or scope.get("brand_id"),
            "lastObservedAt": decorated.get("capturedAt"),
            "lastUpdatedAt": _latest_intake_timestamp(decorated),
            "listingId": match_result.get("targetListingId"),
            "location": _intake_location(decorated),
            "maskedFields": masked_fields,
            "needsReview": decorated.get("stage") == "NEEDS_REVIEW",
            "regionId": decorated.get("regionId") or scope.get("region_id"),
            "tenantId": decorated.get("tenantId") or scope.get("tenant_id"),
            "restrictedData": bool(masked_fields)
            or bool(decorated.get("restrictedData")),
            "retryable": bool(failure.get("retryable")),
            "issue": failure.get("summary")
            or match_result.get("summary")
            or decorated.get("policyReason"),
        }
    )
    return decorated


def _intake_query_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _encode_intake_cursor(*, offset: int, fingerprint: str) -> str:
    payload = {
        "version": 1,
        "offset": offset,
        "fingerprint": fingerprint,
        "expiresAt": (datetime.now(UTC) + _INTAKE_CURSOR_TTL).isoformat(),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def _decode_intake_cursor(cursor: str, *, fingerprint: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if payload.get("version") != 1 or payload.get("fingerprint") != fingerprint:
            raise ValueError("cursor query mismatch")
        if datetime.fromisoformat(payload["expiresAt"]) <= datetime.now(UTC):
            raise ValueError("cursor expired")
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError("cursor offset invalid")
        return offset
    except (
        AttributeError,
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CURSOR_INVALID: cursor is invalid, expired, or belongs to another query",
        ) from exc


def _intake_bool_matches(actual: bool, expected: bool | None) -> bool:
    return expected is None or actual is expected


def _timestamp_in_range(
    value: str | None,
    *,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if not start and not end:
        return True
    if not value:
        return False
    try:
        current = _parse_intake_timestamp(value, field_name="record timestamp")
    except HTTPException:
        return False
    return current is not None and (not start or current >= start) and (
        not end or current <= end
    )


def _parse_intake_timestamp(
    value: str,
    *,
    field_name: str,
) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"VALIDATION_FAILED: {field_name} must be an ISO-8601 timestamp",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def is_record_owner(principal: Principal, record: dict[str, Any]) -> bool:
    owner = record.get("owner")
    submitter = record.get("submitter")
    sentinels = {"system", "unassigned", "SYSTEM", "UNASSIGNED", None, ""}
    ownership_subjects = {
        subject for subject in (owner, submitter) if subject not in sentinels
    }
    return principal.subject_id in ownership_subjects


class NetworkListingActorPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    actorRoleId: str = "expansionManager"
    actorName: str | None = None
    reason: str | None = None


class RiskDisclosurePayload(NetworkListingActorPayload):
    """Actor payload for a high-impact write that must disclose its risk.

    ``riskSummary`` is the text the caller actually showed the operator, and
    ``riskAcknowledged`` records that they accepted it. Both are caller-owned
    on purpose: the server cannot attest to a disclosure it wrote itself.
    """

    riskSummary: str | None = None
    riskAcknowledged: bool = False


class NetworkListingMergePayload(RiskDisclosurePayload):
    targetListingId: str


class IntakeSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str
    heatZoneId: str | None = None
    actorRoleId: str = "expansionManager"
    actorName: str | None = None


class IntakeCorrectPayload(RiskDisclosurePayload):
    fields: dict[str, Any]


class IntakeDecidePayload(RiskDisclosurePayload):
    action: str


class IntakePromotePayload(RiskDisclosurePayload):
    """Promotion carries no extra fields beyond the risk disclosure."""


def create_network_listings_sub_router(
    service: NetworkListingService,
    *,
    require_view_permission_fn: Callable[..., Any],
    require_write_permission_fn: Callable[..., Any],
    audit_log: InMemoryAuditLog | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/network-listings")

    def reject_sensitive_submission_material(body: IntakeSubmitPayload) -> None:
        payload = body.model_dump()
        payload.update(body.model_extra or {})
        offenders = contains_sensitive_submission_material(payload)
        if offenders:
            fields = ", ".join(sorted(offenders))
            raise HTTPException(
                status_code=422,
                detail=(
                    "assisted listing intake does not accept credentials, "
                    f"cookies, bearer tokens, or private API endpoints: {fields}"
                ),
            )

    def get_principal(request: Request) -> Any:
        principal = getattr(request.state, "operator_principal", None)
        if principal is None:
            from apps.api.oday_api.security.dependencies import principal_from_headers

            principal = principal_from_headers(request.headers)
        return principal

    def get_operator_role_id(request: Request) -> str | None:
        return getattr(request.state, "operator_role_id", None)

    @router.get("", dependencies=[Depends(require_view_permission_fn)])
    @router.get("/", dependencies=[Depends(require_view_permission_fn)])
    def get_network_listings(
        request: Request,
        selected_heat_zone_id: str | None = Query(default=None, alias="selectedHeatZoneId"),
        lens: str | None = None,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        authorize_intake_action(
            principal,
            "view",
            operator_role_id=operator_role_id,
            audit_log=audit_log,
            correlation_id=x_correlation_id,
        )
        snap = service.snapshot(
            selected_heat_zone_id=selected_heat_zone_id,
            lens=lens,
            correlation_id=x_correlation_id,
        )

        is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
            "expansion-manager",
            "expansionManager",
            "site-reviewer",
            "siteReviewer",
            "executive",
        )
        is_staff = (
            principal.has_role(Role.EXPANSION_USER)
            or operator_role_id in (
                "expansion-staff",
                "expansionStaff",
                "expansion-user",
                "expansion_user",
            )
        ) and not is_manager

        if is_staff:
            if "listings" in snap:
                snap["listings"] = [
                    lst for lst in snap["listings"]
                    if is_record_owner(principal, lst)
                ]
            if "assistedIntakes" in snap:
                snap["assistedIntakes"] = [
                    intake for intake in snap["assistedIntakes"]
                    if is_record_owner(principal, intake)
                ]

        if "listings" in snap:
            snap["listings"] = [mask_listing(principal, lst) for lst in snap["listings"]]
        if "assistedIntakes" in snap:
            snap["assistedIntakes"] = [
                mask_intake(principal, intake) for intake in snap["assistedIntakes"]
            ]
        return snap

    @router.post(
        "/reset",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def reset_network_listings() -> dict[str, Any]:
        return service.reset()

    @router.post(
        "/listings/{listing_id}/convert",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def convert_listing(
        request: Request,
        listing_id: str,
        body: NetworkListingActorPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            listing = service._listing(listing_id)
            has_legal_hold = listing.get("hasLegalHold") or listing.get("legalHold") or False
            first_actor_id = listing.get("submitter") or listing.get("owner")
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "convert",
                resource=listing,
                risk_acknowledged=body.model_extra.get("riskAcknowledged")
                or getattr(body, "riskAcknowledged", False),
                risk_summary=body.model_extra.get("riskSummary")
                or getattr(body, "riskSummary", None),
                first_actor_id=first_actor_id,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            result = service.convert_listing(
                listing_id=listing_id,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )

            if "listing" in result:
                result["listing"] = mask_listing(principal, result["listing"])
            if "candidate" in result:
                result["candidate"] = mask_listing(principal, result["candidate"])
            return result
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/listings/{listing_id}/merge",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def merge_listing(
        request: Request,
        listing_id: str,
        body: NetworkListingMergePayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            source = service._listing(listing_id)
            target = service._listing(body.targetListingId)

            has_legal_hold = (
                source.get("hasLegalHold")
                or source.get("legalHold")
                or target.get("hasLegalHold")
                or target.get("legalHold")
                or False
            )
            first_actor_id = (
                request.headers.get("x-first-actor-id")
                or source.get("proposer")
                or source.get("submitter")
            )
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "merge",
                resource=source,
                risk_acknowledged=body.riskAcknowledged,
                risk_summary=body.riskSummary,
                first_actor_id=first_actor_id,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )
            authorize_intake_action(
                principal,
                "merge",
                resource=target,
                risk_acknowledged=body.riskAcknowledged,
                risk_summary=body.riskSummary,
                first_actor_id=first_actor_id,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            result = service.merge_listing(
                source_listing_id=listing_id,
                target_listing_id=body.targetListingId,
                reason=body.reason or "",
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )

            if "source" in result:
                result["source"] = mask_listing(principal, result["source"])
            if "target" in result:
                result["target"] = mask_listing(principal, result["target"])
            return result
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/listings/{listing_id}/archive",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def archive_listing(
        request: Request,
        listing_id: str,
        body: NetworkListingActorPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            listing = service._listing(listing_id)
            has_legal_hold = listing.get("hasLegalHold") or listing.get("legalHold") or False
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "cancel",
                resource=listing,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            result = service.archive_listing(
                listing_id=listing_id,
                reason=body.reason or "",
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )

            if "listing" in result:
                result["listing"] = mask_listing(principal, result["listing"])
            return result
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # --- Assisted Ingestion (Intake) Routes (ODP-OC-R5-001) ---

    @router.post(
        "/intake/submit",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def submit_intake(
        request: Request,
        body: IntakeSubmitPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
        x_async_intake: str | None = Header(default=None, alias="X-Async-Intake"),
    ) -> dict[str, Any]:
        try:
            reject_sensitive_submission_material(body)

            # Backpressure Check
            job_queue = getattr(request.app.state, "job_queue", None)
            if job_queue is not None:
                if job_queue.count_active_jobs() >= 200:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="BACKPRESSURE_ACTIVE",
                        headers={"Retry-After": "30"},
                    )

            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "submit_url",
                resource={"heatZoneId": body.heatZoneId},
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            is_async = (x_async_intake == "true")

            result = service.submit_intake(
                url=body.url,
                heat_zone_id=body.heatZoneId,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                job_queue=job_queue,
                async_intake=is_async,
                tenant_id=principal.scope.tenant_id if (principal and principal.scope) else None,
            )
            return mask_intake(principal, result)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/intake",
        dependencies=[Depends(require_view_permission_fn)],
    )
    def list_intakes(
        request: Request,
        selected_heat_zone_id: str | None = Query(default=None, alias="selectedHeatZoneId"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=10, alias="pageSize", ge=1, le=100),
        cursor: str = Query(default="", max_length=4096),
        search: str = Query(default="", max_length=200),
        saved_view: str = Query(default="all", alias="savedView"),
        intake_method: str = Query(default="", alias="intakeMethod"),
        intake_stage: str = Query(default="", alias="intakeStage"),
        match_outcome: str = Query(default="", alias="matchOutcome"),
        source_id: str = Query(default="", alias="sourceId"),
        submitted_by: str = Query(default="", alias="submittedBy"),
        owner: str = Query(default=""),
        assignment_status: str = Query(default="", alias="assignmentStatus"),
        needs_review: bool | None = Query(default=None, alias="needsReview"),
        sla_state: str = Query(default="", alias="slaState"),
        heat_zone_id: str = Query(default="", alias="heatZoneId"),
        area_id: str = Query(default="", alias="areaId"),
        observed_from: str = Query(default="", alias="observedFrom"),
        observed_to: str = Query(default="", alias="observedTo"),
        updated_from: str = Query(default="", alias="updatedFrom"),
        updated_to: str = Query(default="", alias="updatedTo"),
        restricted_data: bool | None = Query(default=None, alias="restrictedData"),
        quarantined: bool | None = Query(default=None),
        failed: bool | None = Query(default=None),
        retryable: bool | None = Query(default=None),
        sort_by: str = Query(default="updatedAt", alias="sortBy"),
        sort_order: str = Query(default="desc", alias="sortOrder", pattern="^(asc|desc)$"),
    ) -> dict[str, Any]:
        if saved_view not in _INTAKE_SAVED_VIEWS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"VALIDATION_FAILED: unsupported savedView {saved_view!r}",
            )
        if sort_by not in _INTAKE_SORT_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"VALIDATION_FAILED: unsupported sortBy {sort_by!r}",
            )
        observed_start = _parse_intake_timestamp(
            observed_from, field_name="observedFrom"
        )
        observed_end = _parse_intake_timestamp(
            observed_to, field_name="observedTo"
        )
        updated_start = _parse_intake_timestamp(
            updated_from, field_name="updatedFrom"
        )
        updated_end = _parse_intake_timestamp(updated_to, field_name="updatedTo")
        if observed_start and observed_end and observed_start > observed_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="VALIDATION_FAILED: observedFrom must not be after observedTo",
            )
        if updated_start and updated_end and updated_start > updated_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="VALIDATION_FAILED: updatedFrom must not be after updatedTo",
            )
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")
        authorize_intake_action(
            principal,
            "view",
            resource={"heatZoneId": selected_heat_zone_id} if selected_heat_zone_id else None,
            operator_role_id=operator_role_id,
            audit_log=audit_log,
            correlation_id=correlation_id,
        )
        intakes = service.list_intakes(selected_heat_zone_id=selected_heat_zone_id)

        is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
            "expansion-manager",
            "expansionManager",
            "site-reviewer",
            "siteReviewer",
            "executive",
        )
        is_staff = (
            principal.has_role(Role.EXPANSION_USER)
            or operator_role_id in (
                "expansion-staff",
                "expansionStaff",
                "expansion-user",
                "expansion_user",
            )
        ) and not is_manager

        if is_staff:
            intakes = [
                intake for intake in intakes
                if is_record_owner(principal, intake)
            ]
        visible = [
            _decorate_intake_for_inbox(mask_intake(principal, intake))
            for intake in intakes
        ]
        processing_stages = {
            "SUBMITTED",
            "CHECKING_IDENTITY",
            "CHECKING_SOURCE_POLICY",
            "RETRIEVING",
            "PARSING",
            "MATCHING",
        }
        counts = {
            "needsReview": sum(item.get("stage") == "NEEDS_REVIEW" for item in visible),
            "awaitingEntry": sum(item.get("stage") == "AWAITING_ASSISTED_ENTRY" for item in visible),
            "processing": sum(item.get("stage") in processing_stages for item in visible),
            "blocked": sum(item.get("stage") in {"QUARANTINED", "FAILED"} for item in visible),
            "ready": sum(item.get("stage") == "READY" for item in visible),
        }

        if saved_view == "needsReview":
            visible = [i for i in visible if i.get("stage") == "NEEDS_REVIEW"]
        elif saved_view == "awaitingEntry":
            visible = [
                i for i in visible if i.get("stage") == "AWAITING_ASSISTED_ENTRY"
            ]
        elif saved_view == "blocked":
            visible = [
                i for i in visible if i.get("stage") in {"QUARANTINED", "FAILED"}
            ]
        elif saved_view == "processing":
            visible = [i for i in visible if i.get("stage") in processing_stages]
        elif saved_view == "ready":
            visible = [i for i in visible if i.get("stage") == "READY"]
        if intake_method:
            visible = [i for i in visible if i.get("intakeMethod") == intake_method]
        if intake_stage:
            visible = [i for i in visible if i.get("stage") == intake_stage]
        if match_outcome:
            visible = [
                i
                for i in visible
                if (i.get("matchResult") or {}).get("outcome") == match_outcome
            ]
        if sla_state:
            visible = [i for i in visible if i.get("slaState") == sla_state]
        if source_id:
            visible = [i for i in visible if i.get("sourceId") == source_id]
        if submitted_by:
            visible = [i for i in visible if i.get("submitter") == submitted_by]
        if owner:
            visible = [i for i in visible if i.get("owner") == owner]
        if assignment_status:
            visible = [
                i for i in visible if i.get("assignmentStatus") == assignment_status
            ]
        if needs_review is not None:
            visible = [
                i for i in visible if bool(i.get("needsReview")) is needs_review
            ]
        effective_heat_zone_id = heat_zone_id or selected_heat_zone_id or ""
        if effective_heat_zone_id:
            visible = [
                i
                for i in visible
                if i.get("heatZoneId") == effective_heat_zone_id
            ]
        if area_id:
            visible = [i for i in visible if i.get("assignedAreaId") == area_id]
        visible = [
            i
            for i in visible
            if _timestamp_in_range(
                i.get("lastObservedAt"), start=observed_start, end=observed_end
            )
            and _timestamp_in_range(
                i.get("lastUpdatedAt"), start=updated_start, end=updated_end
            )
            and _intake_bool_matches(
                bool(i.get("restrictedData")), restricted_data
            )
            and _intake_bool_matches(i.get("stage") == "QUARANTINED", quarantined)
            and _intake_bool_matches(i.get("stage") == "FAILED", failed)
            and _intake_bool_matches(bool(i.get("retryable")), retryable)
        ]
        if search:
            needle = search.casefold()
            visible = [
                i
                for i in visible
                if any(
                    needle in str(i.get(key) or "").casefold()
                    for key in (
                        "id",
                        "listingId",
                        "originalUrl",
                        "canonicalUrl",
                        "sourceId",
                        "submitter",
                        "owner",
                        "heatZoneId",
                        "assignedAreaId",
                        "issue",
                    )
                )
            ]
        sort_keys: dict[str, Callable[[dict[str, Any]], str]] = {
            "id": lambda i: str(i.get("id") or ""),
            "sourceId": lambda i: str(i.get("sourceId") or ""),
            "intakeMethod": lambda i: str(i.get("intakeMethod") or ""),
            "stage": lambda i: str(i.get("stage") or ""),
            "matchOutcome": lambda i: str(
                (i.get("matchResult") or {}).get("outcome") or ""
            ),
            "owner": lambda i: str(i.get("owner") or ""),
            "submitter": lambda i: str(i.get("submitter") or ""),
            "assignmentStatus": lambda i: str(i.get("assignmentStatus") or ""),
            "slaState": lambda i: str(i.get("slaState") or ""),
            "dueAt": lambda i: str(
                i.get("dueAt")
                or ("9999-12-31T23:59:59Z" if sort_order == "asc" else "")
            ),
            "observedAt": lambda i: str(i.get("lastObservedAt") or ""),
            "updatedAt": lambda i: str(i.get("lastUpdatedAt") or ""),
        }
        visible.sort(
            key=lambda i: (sort_keys[sort_by](i).casefold(), str(i.get("id") or "")),
            reverse=sort_order == "desc",
        )
        total = len(visible)
        fingerprint = _intake_query_fingerprint(
            {
                "areaId": area_id,
                "assignmentStatus": assignment_status,
                "failed": failed,
                "heatZoneId": effective_heat_zone_id,
                "intakeMethod": intake_method,
                "intakeStage": intake_stage,
                "matchOutcome": match_outcome,
                "needsReview": needs_review,
                "observedFrom": observed_from,
                "observedTo": observed_to,
                "owner": owner,
                "quarantined": quarantined,
                "restrictedData": restricted_data,
                "retryable": retryable,
                "savedView": saved_view,
                "search": search,
                "slaState": sla_state,
                "sortBy": sort_by,
                "sortOrder": sort_order,
                "sourceId": source_id,
                "submittedBy": submitted_by,
                "updatedFrom": updated_from,
                "updatedTo": updated_to,
            }
        )
        start = (
            _decode_intake_cursor(cursor, fingerprint=fingerprint)
            if cursor
            else (page - 1) * page_size
        )
        page_items = visible[start:start + page_size]
        evidence_state = (
            "degraded"
            if any(i.get("failure") for i in page_items)
            else (
                "partial"
                if any(not i.get("rawSnapshot") for i in page_items)
                else "complete"
            )
        )
        next_offset = start + len(page_items)
        previous_offset = max(0, start - page_size)
        return {
            "items": page_items,
            "total": total,
            "page": (start // page_size) + 1,
            "pageSize": page_size,
            "counts": counts,
            "evidenceState": evidence_state,
            "nextCursor": (
                _encode_intake_cursor(offset=next_offset, fingerprint=fingerprint)
                if next_offset < total
                else None
            ),
            "previousCursor": (
                _encode_intake_cursor(
                    offset=previous_offset, fingerprint=fingerprint
                )
                if start > 0
                else None
            ),
            "queryFingerprint": fingerprint,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }

    @router.get(
        "/intake/{intake_id}",
        dependencies=[Depends(require_view_permission_fn)],
    )
    def get_intake(request: Request, intake_id: str) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = service.get_intake(intake_id)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get("X-Correlation-Id")
            authorize_intake_action(
                principal,
                "view",
                resource=intake,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=correlation_id,
            )
            return mask_intake(principal, intake)
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post(
        "/intake/{intake_id}/correct",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def correct_intake(
        request: Request,
        intake_id: str,
        body: IntakeCorrectPayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = service.get_intake(intake_id)

            is_identity_affecting = any(
                k in {"providerListingId", "address", "rent", "areaPing"} for k in body.fields
            )
            first_actor_id = intake.get("submitter")
            has_legal_hold = intake.get("hasLegalHold") or intake.get("legalHold") or False
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "correct",
                resource=intake,
                risk_acknowledged=body.riskAcknowledged,
                risk_summary=body.riskSummary,
                first_actor_id=first_actor_id,
                is_identity_affecting=is_identity_affecting,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            result = service.correct_intake(
                intake_id=intake_id,
                fields=body.fields,
                reason=body.reason,
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )
            return mask_intake(principal, result)
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/intake/{intake_id}/decide",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def decide_intake(
        request: Request,
        intake_id: str,
        body: IntakeDecidePayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = service.get_intake(intake_id)
            first_actor_id = intake.get("submitter")
            has_legal_hold = intake.get("hasLegalHold") or intake.get("legalHold") or False
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "decide",
                resource=intake,
                risk_acknowledged=body.riskAcknowledged,
                risk_summary=body.riskSummary,
                first_actor_id=first_actor_id,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            result = service.decide_intake(
                intake_id=intake_id,
                action=body.action,
                reason=body.reason,
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                target_listing_id=getattr(body, "targetListingId", None),
            )
            return mask_intake(principal, result)
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/intake/{intake_id}/retry",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def retry_intake(
        request: Request,
        intake_id: str,
        body: NetworkListingActorPayload,
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = service.get_intake(intake_id)
            actor_name = body.actorName or principal.subject_id

            if intake.get("stage") == "QUARANTINED":
                action = "reopen_quarantine"
            else:
                action = "reopen_failed"

            authorize_intake_action(
                principal,
                action,
                resource=intake,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            job_queue = getattr(request.app.state, "job_queue", None)
            result = service.retry_intake(
                intake_id=intake_id,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                correlation_id=x_correlation_id,
                job_queue=job_queue,
                tenant_id=principal.scope.tenant_id if (principal and principal.scope) else None,
            )
            return mask_intake(principal, result)
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/intake/{intake_id}/promote",
        dependencies=[Depends(require_write_permission_fn)],
    )
    def promote_intake(
        request: Request,
        intake_id: str,
        body: IntakePromotePayload,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = service.get_intake(intake_id)
            first_actor_id = intake.get("submitter")
            has_legal_hold = intake.get("hasLegalHold") or intake.get("legalHold") or False
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "promote",
                resource=intake,
                risk_acknowledged=body.riskAcknowledged,
                risk_summary=body.riskSummary,
                first_actor_id=first_actor_id,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            result = service.promote_intake(
                intake_id=intake_id,
                reason=body.reason,
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )

            if "listing" in result:
                result["listing"] = mask_listing(principal, result["listing"])
            if "candidate" in result:
                result["candidate"] = mask_listing(principal, result["candidate"])
            return result
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except NetworkListingConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


__all__ = ["create_network_listings_sub_router"]
