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

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.routes._common import reset_allowed_guard
from apps.api.app.routes.operator_modules.live_service import resolve_service
from modules.external_data.security import contains_sensitive_submission_material
from modules.listing.application.intake_authorization import (
    authorize_intake_action,
    collection_rows_are_scope_projected,
    intake_resource_in_scope,
    mask_intake,
    mask_listing,
)
from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowUnavailable
from modules.listing.domain.intake_states import AssignmentState
from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
    NetworkListingNotFound,
    NetworkListingPolicyError,
    NetworkListingService,
)
from modules.opsboard.application.network_read_scope import (
    project_intake_record,
    project_listing_snapshot,
)
from modules.opsboard.application.operator_actor import operator_actor
from shared.audit import InMemoryAuditLog
from shared.auth import DataClassification, Principal, Role
from shared.infrastructure.persistence.assisted_listing_intake import (
    AssistedIntakePersistenceConflict,
)
from shared.jobs.queue import JobFenceRejectedError


def _visible_inbox_value(item: dict[str, Any], key: str) -> Any:
    """Filtering/search must not make a withheld value observable through membership."""
    if item.get(f"{key}_masked") is True or key in (item.get("masked_fields") or []):
        return None
    if any(field.get("field_path") == key and field.get("masked") is True
           for field in (item.get("fields") or []) if isinstance(field, dict)):
        return None
    return item.get(key)


def _inbox_instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return instant.astimezone(UTC) if instant.utcoffset() is not None else None
    except ValueError:
        return None


def _inbox_query_instant(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    instant = _inbox_instant(value)
    if instant is None:
        raise HTTPException(422, f"{name} requires an ISO 8601 timestamp with timezone")
    return instant


def _inbox_visibility(item: dict[str, Any]) -> tuple[bool, bool | None]:
    fields = [field for field in (item.get("fields") or []) if isinstance(field, dict)]
    parsed = item.get("parsedFields") or {}
    parsed = parsed if isinstance(parsed, dict) else {}
    masked = bool(item.get("masked_fields")) or any(
        value is True for key, value in item.items() if key.endswith("_masked")
    ) or any(field.get("masked") is True for field in fields) or any(
        isinstance(field, dict) and field.get("masked") is True for field in parsed.values()
    )
    restricted = None if not fields and not parsed else False
    for field in fields:
        classification = field.get("classification", "RESTRICTED")
        try:
            level = (classification if isinstance(classification, DataClassification)
                     else DataClassification[str(classification).upper()])
        except (KeyError, TypeError):
            level = DataClassification.HIGHLY_RESTRICTED
        if level >= DataClassification.RESTRICTED:
            restricted = True
    # These are the existing parsedFields classifications in mask_intake.
    if any(key in parsed for key in ("contactPhone", "brokerEmail", "privateNotes")):
        restricted = True
    return masked, restricted


def _project_inbox_visibility(item: dict[str, Any]) -> dict[str, Any]:
    projected = dict(item)
    for key in ("sourceId", "submittedBy", "ownerSubjectId", "assignedAreaId", "assignmentStatus",
                "submittedAt", "updatedAt", "lastObservedAt", "dueAt", "canonicalUrl", "failure"):
        if key in item:
            projected[key] = _visible_inbox_value(item, key)
    for authority, legacy in (("submittedBy", "submitter"), ("ownerSubjectId", "owner")):
        if item.get(authority) is not None and projected.get(authority) is None:
            projected[legacy] = None
    projected["masked"], projected["restricted"] = _inbox_visibility(item)
    return projected


def is_record_owner(principal: Principal, record: dict[str, Any]) -> bool:
    owner = record.get("owner")
    submitter = record.get("submitter")
    sentinels = {"system", "unassigned", "SYSTEM", "UNASSIGNED", None, ""}
    ownership_subjects = {subject for subject in (owner, submitter) if subject not in sentinels}
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


class NetworkListingReadResponse(BaseModel):
    """The recorded hard-rule verdict; other authorized listing fields survive."""

    model_config = ConfigDict(extra="allow")

    hardRuleFailures: list[str] | None = Field(
        default=None,
        description=(
            "Recorded hard-rule failures. Null or absent means unmeasured, not passed. "
            "An empty array means a recorded check found no failures; a nonempty "
            "array names recorded failures. This field alone does not authorize promotion."
        ),
    )


class NetworkListingRadarReadResponse(BaseModel):
    """Narrow read contract preserving the existing authorized snapshot fields."""

    model_config = ConfigDict(extra="allow")

    listings: list[NetworkListingReadResponse]


def create_network_listings_sub_router(
    service: NetworkListingService,
    *,
    require_view_permission_fn: Callable[..., Any],
    require_write_permission_fn: Callable[..., Any],
    audit_log: InMemoryAuditLog | None = None,
    service_resolver: Callable[[Request], Any] | None = None,
    allow_reset: bool = True,
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

    def active_service(request: Request) -> Any:
        return resolve_service(request, service, service_resolver)

    require_reset_allowed = reset_allowed_guard(
        allow_reset=allow_reset,
        resource_label="network listing",
    )

    @router.get("", dependencies=[Depends(require_view_permission_fn)],
                response_model=NetworkListingRadarReadResponse)
    @router.get("/", dependencies=[Depends(require_view_permission_fn)],
                response_model=NetworkListingRadarReadResponse)
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
            collection_scope=(
                {"heatZoneId": selected_heat_zone_id} if selected_heat_zone_id else None
            ),
            operator_role_id=operator_role_id,
            audit_log=audit_log,
            correlation_id=x_correlation_id,
            tenant_id=principal.tenant_id,
        )
        snap = active_service(request).snapshot(
            selected_heat_zone_id=selected_heat_zone_id,
            lens=lens,
            correlation_id=x_correlation_id,
            tenant_id=principal.tenant_id,
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
            or operator_role_id
            in (
                "expansion-staff",
                "expansionStaff",
                "expansion-user",
                "expansion_user",
            )
        ) and not is_manager

        if is_staff:
            if "listings" in snap:
                snap["listings"] = [
                    lst for lst in snap["listings"] if is_record_owner(principal, lst)
                ]
            if "assistedIntakes" in snap:
                snap["assistedIntakes"] = [
                    intake
                    for intake in snap["assistedIntakes"]
                    if is_record_owner(principal, intake)
                ]

        if collection_rows_are_scope_projected(principal):
            snap = project_listing_snapshot(principal, snap)

        if "listings" in snap:
            snap["listings"] = [mask_listing(principal, lst) for lst in snap["listings"]]
        if "assistedIntakes" in snap:
            snap["assistedIntakes"] = [
                mask_intake(principal, intake) for intake in snap["assistedIntakes"]
            ]
        return snap

    @router.post(
        "/reset",
        dependencies=[
            Depends(require_write_permission_fn),
            Depends(require_reset_allowed),
        ],
    )
    def reset_network_listings(request: Request) -> dict[str, Any]:
        return active_service(request).reset()

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
            listing = active_service(request)._listing(listing_id)
            has_legal_hold = listing.get("hasLegalHold") or listing.get("legalHold") or False
            first_actor_id = listing.get("submitter") or listing.get("owner")
            actor = operator_actor(principal)

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

            result = active_service(request).convert_listing(
                listing_id=listing_id,
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )

            if "listing" in result:
                result["listing"] = mask_listing(principal, result["listing"])
            if "candidate" in result:
                result["candidate"] = mask_listing(principal, result["candidate"])
            return result
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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
            source = active_service(request)._listing(listing_id)
            target = active_service(request)._listing(body.targetListingId)

            has_legal_hold = (
                source.get("hasLegalHold")
                or source.get("legalHold")
                or target.get("hasLegalHold")
                or target.get("legalHold")
                or False
            )
            first_actor_id = (
                source.get("proposer")
                or source.get("submitter")
            )
            actor = operator_actor(principal)

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

            result = active_service(request).merge_listing(
                source_listing_id=listing_id,
                target_listing_id=body.targetListingId,
                reason=body.reason or "",
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )

            if "source" in result:
                result["source"] = mask_listing(principal, result["source"])
            if "target" in result:
                result["target"] = mask_listing(principal, result["target"])
            return result
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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
            listing = active_service(request)._listing(listing_id)
            has_legal_hold = listing.get("hasLegalHold") or listing.get("legalHold") or False
            actor = operator_actor(principal)

            authorize_intake_action(
                principal,
                "archive",
                resource=listing,
                has_legal_hold=has_legal_hold,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
            )

            result = active_service(request).archive_listing(
                listing_id=listing_id,
                reason=body.reason or "",
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
            )

            if "listing" in result:
                result["listing"] = mask_listing(principal, result["listing"])
            return result
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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
            actor = operator_actor(principal)

            # Submitting a URL creates a new intake inside the caller's scope;
            # there is no existing object to read a tenant from, so the heat
            # zone travels as a collection filter and the verified principal
            # tenant is the declared query scope.
            authorize_intake_action(
                principal,
                "submit_url",
                collection_scope={"heatZoneId": body.heatZoneId},
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=x_correlation_id,
                tenant_id=principal.tenant_id,
            )

            is_async = x_async_intake == "true"

            result = active_service(request).submit_intake(
                url=body.url,
                heat_zone_id=body.heatZoneId,
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                job_queue=job_queue,
                async_intake=is_async,
                tenant_id=actor.tenant_id,
                authorize_existing=lambda intake: authorize_intake_action(
                    principal, "view", resource=intake,
                    operator_role_id=operator_role_id, audit_log=audit_log,
                    correlation_id=x_correlation_id,
                ),
            )
            return mask_intake(principal, result)
        except IntakeWorkflowUnavailable as exc:
            raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (NetworkListingConflict, AssistedIntakePersistenceConflict) as exc:
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
        search: str = Query(default="", max_length=200),
        saved_view: str = Query(default="all", alias="savedView"),
        intake_method: str = Query(default="", alias="intakeMethod"),
        intake_stage: str = Query(default="", alias="intakeStage"),
        match_outcome: str = Query(default="", alias="matchOutcome"),
        sla_state: str = Query(default="", alias="slaState"),
        source_id: str = Query(default="", alias="sourceId", max_length=200),
        submitted_by: str = Query(default="", alias="submittedBy", max_length=200),
        owner_id: str = Query(default="", alias="ownerId", max_length=200),
        assignment_status: Annotated[AssignmentState | None, Query(alias="assignmentStatus")] = None,
        assigned_area_id: str = Query(default="", alias="assignedAreaId", max_length=200),
        observed_from: str | None = Query(default=None, alias="observedFrom", max_length=64, json_schema_extra={"format": "date-time"}),
        observed_to: str | None = Query(default=None, alias="observedTo", max_length=64, json_schema_extra={"format": "date-time"}),
        updated_from: str | None = Query(default=None, alias="updatedFrom", max_length=64, json_schema_extra={"format": "date-time"}),
        updated_to: str | None = Query(default=None, alias="updatedTo", max_length=64, json_schema_extra={"format": "date-time"}),
        masked: bool | None = Query(default=None),
        restricted: bool | None = Query(default=None),
        retryable: bool | None = Query(default=None),
        sort_by: str = Query(default="updatedAt", alias="sortBy"),
        sort_order: str = Query(default="desc", alias="sortOrder", pattern="^(asc|desc)$"),
    ) -> dict[str, Any]:
        observed_start = _inbox_query_instant(observed_from, "observedFrom")
        observed_end = _inbox_query_instant(observed_to, "observedTo")
        updated_start = _inbox_query_instant(updated_from, "updatedFrom")
        updated_end = _inbox_query_instant(updated_to, "updatedTo")
        if ((observed_start and observed_end and observed_start > observed_end)
                or (updated_start and updated_end and updated_start > updated_end)):
            raise HTTPException(422, "Date range start must not follow its end")
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        correlation_id = request.headers.get("x-correlation-id") or request.headers.get(
            "X-Correlation-Id"
        )
        # Listing intakes is a collection query: the heat zone is a filter on
        # that query, never a stand-in target resource.
        authorize_intake_action(
            principal,
            "view",
            collection_scope={
                **({"heatZoneId": selected_heat_zone_id} if selected_heat_zone_id else {}),
                **({"assignedAreaId": assigned_area_id} if assigned_area_id else {}),
            } or None,
            operator_role_id=operator_role_id,
            audit_log=audit_log,
            correlation_id=correlation_id,
            tenant_id=principal.tenant_id,
        )
        intakes = active_service(request).list_intakes(
            selected_heat_zone_id=selected_heat_zone_id,
            tenant_id=principal.tenant_id,
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
            or operator_role_id
            in (
                "expansion-staff",
                "expansionStaff",
                "expansion-user",
                "expansion_user",
            )
        ) and not is_manager

        if is_staff:
            intakes = [intake for intake in intakes if is_record_owner(principal, intake)]
        intakes = [i for i in intakes if intake_resource_in_scope(principal, i)]
        if collection_rows_are_scope_projected(principal):
            intakes = [project_intake_record(intake) for intake in intakes]
        visible = [mask_intake(principal, intake) for intake in intakes]
        visible = [_project_inbox_visibility(item) for item in visible]
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
            "awaitingEntry": sum(
                item.get("stage") == "AWAITING_ASSISTED_ENTRY" for item in visible
            ),
            "processing": sum(item.get("stage") in processing_stages for item in visible),
            "blocked": sum(item.get("stage") in {"QUARANTINED", "FAILED"} for item in visible),
            "ready": sum(item.get("stage") == "READY" for item in visible),
        }

        if saved_view == "needsReview":
            visible = [i for i in visible if i.get("stage") == "NEEDS_REVIEW"]
        elif saved_view == "awaitingEntry":
            visible = [i for i in visible if i.get("stage") == "AWAITING_ASSISTED_ENTRY"]
        elif saved_view == "blocked":
            visible = [i for i in visible if i.get("stage") in {"QUARANTINED", "FAILED"}]
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
                i for i in visible if (i.get("matchResult") or {}).get("outcome") == match_outcome
            ]
        if sla_state:
            visible = [i for i in visible if i.get("slaState") == sla_state]
        for key, expected in (
            ("sourceId", source_id), ("submittedBy", submitted_by),
            ("ownerSubjectId", owner_id), ("assignmentStatus", assignment_status),
            ("assignedAreaId", assigned_area_id),
        ):
            if expected:
                visible = [i for i in visible if _visible_inbox_value(i, key) == expected]
        for key, start_at, end_at in (
            ("lastObservedAt", observed_start, observed_end),
            ("updatedAt", updated_start, updated_end),
        ):
            if start_at is not None or end_at is not None:
                visible = [i for i in visible
                           if (at := _inbox_instant(_visible_inbox_value(i, key))) is not None
                           and (start_at is None or at >= start_at)
                           and (end_at is None or at <= end_at)]
        for key, expected in (("masked", masked), ("restricted", restricted)):
            if expected is not None:
                visible = [i for i in visible if i.get(key) is expected]
        if retryable is not None:
            visible = [i for i in visible
                       if _visible_inbox_value(i, "failure") is not None
                       and isinstance(i["failure"], dict)
                       and i["failure"].get("retryable") is retryable]
        if search:
            needle = search.casefold()
            visible = [
                i
                for i in visible
                if any(
                    needle in str(_visible_inbox_value(i, k) or "").casefold()
                    for k in ("id", "canonicalUrl", "sourceId", "submitter", "owner", "submittedBy", "ownerSubjectId")
                )
            ]
        sort_key = sort_by if sort_by in {
            "id", "stage", "sourceId", "updatedAt", "submittedAt", "lastObservedAt", "dueAt",
        } else "updatedAt"
        def ordering_value(item: dict[str, Any]) -> Any:
            value = _visible_inbox_value(item, sort_key)
            if sort_key in {"updatedAt", "submittedAt", "lastObservedAt", "dueAt"}:
                return _inbox_instant(value)
            return value if isinstance(value, str) and value else None
        known = [item for item in visible if ordering_value(item) is not None]
        unknown = [item for item in visible if ordering_value(item) is None]
        known.sort(key=lambda i: (ordering_value(i), i.get("id", "")), reverse=sort_order == "desc")
        unknown.sort(key=lambda i: i.get("id", ""))
        visible = known + unknown
        total = len(visible)
        start = (page - 1) * page_size
        page_items = visible[start : start + page_size]
        evidence_state = (
            "degraded"
            if any(i.get("failure") for i in page_items)
            else ("partial" if any(not i.get("rawSnapshot") for i in page_items) else "complete")
        )
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "counts": counts,
            "evidenceState": evidence_state,
        }

    @router.get(
        "/intake/{intake_id}",
        dependencies=[Depends(require_view_permission_fn)],
    )
    def get_intake(request: Request, intake_id: str) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = active_service(request).get_intake(intake_id)
            correlation_id = request.headers.get("x-correlation-id") or request.headers.get(
                "X-Correlation-Id"
            )
            authorize_intake_action(
                principal,
                "view",
                resource=intake,
                operator_role_id=operator_role_id,
                audit_log=audit_log,
                correlation_id=correlation_id,
            )
            if principal.has_role(Role.OPERATOR_VIEWER):
                intake = project_intake_record(intake)
            return mask_intake(principal, intake)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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
            intake = active_service(request).get_intake(intake_id)

            is_identity_affecting = any(
                k in {"providerListingId", "address", "rent", "areaPing"} for k in body.fields
            )
            actor = operator_actor(principal)

            def authorize_existing(current: dict[str, Any]) -> None:
                authorize_intake_action(
                    principal,
                    "correct",
                    resource=current,
                    risk_acknowledged=body.riskAcknowledged,
                    risk_summary=body.riskSummary,
                    first_actor_id=current.get("submitter"),
                    is_identity_affecting=is_identity_affecting,
                    has_legal_hold=bool(current.get("hasLegalHold") or current.get("legalHold")),
                    operator_role_id=operator_role_id,
                    audit_log=audit_log,
                    correlation_id=x_correlation_id,
                )

            authorize_existing(intake)

            result = active_service(request).correct_intake(
                intake_id=intake_id,
                fields=body.fields,
                reason=body.reason,
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                authorize_existing=authorize_existing,
            )
            return mask_intake(principal, result)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (NetworkListingConflict, AssistedIntakePersistenceConflict) as exc:
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
            intake = active_service(request).get_intake(intake_id)
            actor = operator_actor(principal)

            def authorize_existing(current: dict[str, Any]) -> None:
                authorize_intake_action(
                    principal,
                    "decide",
                    resource=current,
                    risk_acknowledged=body.riskAcknowledged,
                    risk_summary=body.riskSummary,
                    first_actor_id=current.get("submitter"),
                    has_legal_hold=bool(current.get("hasLegalHold") or current.get("legalHold")),
                    operator_role_id=operator_role_id,
                    audit_log=audit_log,
                    correlation_id=x_correlation_id,
                )

            authorize_existing(intake)

            result = active_service(request).decide_intake(
                intake_id=intake_id,
                action=body.action,
                reason=body.reason,
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                target_listing_id=getattr(body, "targetListingId", None),
                authorize_existing=authorize_existing,
            )
            return mask_intake(principal, result)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (NetworkListingConflict, AssistedIntakePersistenceConflict) as exc:
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
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    ) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = active_service(request).get_intake(intake_id)
            actor = operator_actor(principal)

            def authorize_retry(current: dict[str, Any]) -> None:
                authorize_intake_action(
                    principal,
                    "reopen_quarantine" if current.get("stage") == "QUARANTINED" else "reopen_failed",
                    resource=current,
                    operator_role_id=operator_role_id,
                    audit_log=audit_log,
                    correlation_id=x_correlation_id,
                )

            authorize_retry(intake)

            job_queue = getattr(request.app.state, "job_queue", None)
            result = active_service(request).retry_intake(
                intake_id=intake_id,
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                correlation_id=x_correlation_id,
                job_queue=job_queue,
                tenant_id=actor.tenant_id,
                idempotency_key=idempotency_key,
                authorize_existing=authorize_retry,
            )
            return mask_intake(principal, result)
        except IntakeWorkflowUnavailable as exc:
            raise HTTPException(503, "INTAKE_WORKFLOW_POLICY_UNAVAILABLE") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (NetworkListingConflict, AssistedIntakePersistenceConflict, JobFenceRejectedError) as exc:
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
            intake = active_service(request).get_intake(intake_id)
            actor = operator_actor(principal)

            def authorize_existing(current: dict[str, Any]) -> None:
                authorize_intake_action(
                    principal,
                    "promote",
                    resource=current,
                    risk_acknowledged=body.riskAcknowledged,
                    risk_summary=body.riskSummary,
                    first_actor_id=current.get("submitter"),
                    has_legal_hold=bool(current.get("hasLegalHold") or current.get("legalHold")),
                    operator_role_id=operator_role_id,
                    audit_log=audit_log,
                    correlation_id=x_correlation_id,
                )

            authorize_existing(intake)

            result = active_service(request).promote_intake(
                intake_id=intake_id,
                reason=body.reason,
                risk_summary=body.riskSummary,
                risk_acknowledged=body.riskAcknowledged,
                actor_role_id=actor.role_id,
                actor_name=actor.subject_id,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
                authorize_existing=authorize_existing,
            )

            if "listing" in result:
                result["listing"] = mask_listing(principal, result["listing"])
            if "candidate" in result:
                result["candidate"] = mask_listing(principal, result["candidate"])
            return result
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except NetworkListingNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (NetworkListingConflict, AssistedIntakePersistenceConflict) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except NetworkListingPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


__all__ = ["create_network_listings_sub_router"]
