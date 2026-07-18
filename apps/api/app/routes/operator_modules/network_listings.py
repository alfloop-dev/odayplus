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
        val = getattr(request.state, "operator_role_id", None)
        if val is None:
            val = request.headers.get("x-operator-role")
        return val

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
        authorize_intake_action(principal, "view", operator_role_id=operator_role_id)
        snap = service.snapshot(
            selected_heat_zone_id=selected_heat_zone_id,
            lens=lens,
            correlation_id=x_correlation_id,
        )
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
    ) -> dict[str, Any]:
        try:
            reject_sensitive_submission_material(body)
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            actor_name = body.actorName or principal.subject_id

            authorize_intake_action(
                principal,
                "submit_url",
                resource={"heatZoneId": body.heatZoneId},
                operator_role_id=operator_role_id,
            )

            result = service.submit_intake(
                url=body.url,
                heat_zone_id=body.heatZoneId,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
                idempotency_key=idempotency_key,
                correlation_id=x_correlation_id,
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
    ) -> list[dict[str, Any]]:
        principal = get_principal(request)
        operator_role_id = get_operator_role_id(request)
        authorize_intake_action(
            principal,
            "view",
            resource={"heatZoneId": selected_heat_zone_id} if selected_heat_zone_id else None,
            operator_role_id=operator_role_id,
        )
        intakes = service.list_intakes(selected_heat_zone_id=selected_heat_zone_id)
        return [mask_intake(principal, intake) for intake in intakes]

    @router.get(
        "/intake/{intake_id}",
        dependencies=[Depends(require_view_permission_fn)],
    )
    def get_intake(request: Request, intake_id: str) -> dict[str, Any]:
        try:
            principal = get_principal(request)
            operator_role_id = get_operator_role_id(request)
            intake = service.get_intake(intake_id)
            authorize_intake_action(
                principal, "view", resource=intake, operator_role_id=operator_role_id
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
            )

            result = service.retry_intake(
                intake_id=intake_id,
                actor_role_id=body.actorRoleId,
                actor_name=actor_name,
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
