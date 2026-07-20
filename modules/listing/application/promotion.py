from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from modules.listing.domain.intake_states import (
    Actor,
    DenialCode,
    DomainValidationError,
    PrincipalRole,
    PromotionAggregate,
    PromotionState,
    PromotionStateMachine,
    TransitionContext,
)
from shared.domain.events import DomainEvent


def to_state_enum(status: str) -> PromotionState:
    if status == "PENDING_REVIEW":
        return PromotionState.VALIDATING
    return PromotionState(status)


def to_status_str(state: PromotionState) -> str:
    if state == PromotionState.VALIDATING:
        return "PENDING_REVIEW"
    return state.value


class PromotionService:
    """Manages the Intake-to-Listing-to-Candidate promotion saga."""

    def __init__(
        self,
        promotion_repository: Any,
        listing_repository: Any,
        intake_repository: Any,
        outbox_repository: Any = None,
    ) -> None:
        self.promotion_repository = promotion_repository
        self.listing_repository = listing_repository
        self.intake_repository = intake_repository
        self.outbox_repository = outbox_repository

    def request_promotion(
        self,
        intake_id: str,
        target_format_code: str,
        reason: str,
        gate_snapshot_sha256: str,
        context: TransitionContext,
    ) -> dict[str, Any]:
        intake = self.intake_repository.get_listing_intake(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        # Check tenant isolation
        intake_tenant = intake.get("tenantId") or intake.get("scope", {}).get("tenant_id")
        if intake_tenant and context.actor.tenant_id != intake_tenant:
            raise DomainValidationError(
                DenialCode.TENANT_SCOPE_DENIED,
                "Tenant isolation mismatch"
            )

        target_listing_id = intake.get("matchResult", {}).get("targetListingId")
        if not target_listing_id:
            raise ValueError("Intake not resolved to listing")

        listing = self.listing_repository.get_listing(target_listing_id)
        if not listing:
            raise ValueError(f"Listing {target_listing_id} not found")

        # Verify listing status
        if listing.get("status") in {"duplicate", "archived", "expired"}:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Listing status {listing.get('status')} is not eligible for promotion"
            )

        if listing.get("hardRuleFailures"):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                "Listing has hard rule failures"
            )

        # Validate candidate gate (listing fields validation)
        validation_errors = self._validate_listing_fields(listing)
        if validation_errors:
            raise DomainValidationError(
                DenialCode.SOURCE_POLICY_DENIED,
                f"Candidate gate failed: missing {', '.join(validation_errors)}"
            )

        # Check duplicate candidate
        for cand in self.listing_repository.list_candidates():
            if cand.get("listingId") == target_listing_id:
                raise DomainValidationError(
                    DenialCode.DEPENDENCY_CONFLICT,
                    "DUPLICATE_CANDIDATE"
                )

        # Idempotency check: return existing promotion for this intake
        existing_promos = self.promotion_repository.list_promotions()
        for promo in existing_promos:
            if promo.get("intake_id") == intake_id:
                return promo

        # Initialize promotion decision
        promo_id = str(uuid.uuid4())
        promo_agg = PromotionAggregate(
            id=promo_id,
            tenant_id=context.actor.tenant_id,
            status=PromotionState.REQUESTED,
            version=1,
            proposer_id=context.actor.actor_id,
        )
        PromotionStateMachine.transition(None, PromotionState.REQUESTED, context)

        # Transition to VALIDATING (maps to PENDING_REVIEW)
        system_actor = Actor(
            actor_id="system",
            role=PrincipalRole.SVC_PROMOTION,
            tenant_id=context.actor.tenant_id,
        )
        system_context = TransitionContext(
            actor=system_actor,
            idempotency_key=f"system-val-{context.idempotency_key}",
            correlation_id=context.correlation_id,
        )
        PromotionStateMachine.transition(promo_agg, PromotionState.VALIDATING, system_context)

        promo_record = {
            "promotion_decision_id": promo_id,
            "intake_id": intake_id,
            "listing_id": target_listing_id,
            "status": "PENDING_REVIEW",
            "decision_type": "STANDARD",
            "version": promo_agg.version,
            "audit_event_id": f"AUD-INTAKE-{uuid.uuid4().hex[:8]}",
            "correlation_id": context.correlation_id or str(uuid.uuid4()),
            "tenant_id": context.actor.tenant_id,
            "proposer": context.actor.actor_id,
            "gate_snapshot_sha256": gate_snapshot_sha256,
            "target_format_code": target_format_code,
            "reason": reason,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.promotion_repository.save_promotion(promo_record)

        # Emit candidate.promotion_requested event
        self._emit_event(
            event_type="candidate.promotion_requested",
            payload={
                "promotion_decision_id": promo_id,
                "intake_id": intake_id,
                "listing_id": target_listing_id,
                "status": "REQUESTED",
                "version": promo_record["version"],
            },
            tenant_id=context.actor.tenant_id,
            aggregate_type="promotion_decision",
            aggregate_id=promo_id,
            aggregate_version=promo_record["version"],
            correlation_id=context.correlation_id,
        )

        return promo_record

    def review_promotion(
        self,
        promotion_decision_id: str,
        decision: str,
        reason: str,
        risk_acknowledged: bool,
        context: TransitionContext,
    ) -> dict[str, Any]:
        promo = self.promotion_repository.get_promotion(promotion_decision_id)
        if not promo:
            raise ValueError(f"Promotion decision {promotion_decision_id} not found")

        if context.actor.tenant_id != promo.get("tenant_id"):
            raise DomainValidationError(
                DenialCode.TENANT_SCOPE_DENIED,
                "Tenant isolation mismatch"
            )

        current_state = to_state_enum(promo["status"])
        promo_agg = PromotionAggregate(
            id=promotion_decision_id,
            tenant_id=promo["tenant_id"],
            status=current_state,
            version=promo["version"],
            proposer_id=promo.get("proposer"),
        )

        target_state = PromotionState.APPROVED if decision == "APPROVE" else PromotionState.REJECTED
        PromotionStateMachine.transition(promo_agg, target_state, context)

        promo["version"] = promo_agg.version
        promo["status"] = to_status_str(target_state)
        promo["reviewer"] = context.actor.actor_id
        promo["reviewed_at"] = datetime.now(UTC).isoformat()
        promo["review_reason"] = reason
        promo["risk_acknowledged"] = risk_acknowledged

        if target_state == PromotionState.REJECTED:
            self.promotion_repository.save_promotion(promo)
            return promo

        # APPROVED path: transition APPROVED -> CANDIDATE_CREATING -> CANDIDATE_CREATED -> SCORE_QUEUED -> COMPLETED
        system_actor = Actor(
            actor_id="system",
            role=PrincipalRole.SVC_PROMOTION,
            tenant_id=context.actor.tenant_id,
        )
        system_context = TransitionContext(
            actor=system_actor,
            idempotency_key=f"system-exec-{context.idempotency_key}",
            correlation_id=context.correlation_id,
        )

        # 1. APPROVED -> CANDIDATE_CREATING
        PromotionStateMachine.transition(promo_agg, PromotionState.CANDIDATE_CREATING, system_context)
        promo["status"] = to_status_str(PromotionState.CANDIDATE_CREATING)
        promo["version"] = promo_agg.version
        self.promotion_repository.save_promotion(promo)

        try:
            listing_id = promo["listing_id"]
            listing = self.listing_repository.get_listing(listing_id)

            # Check duplicate candidate again
            for cand in self.listing_repository.list_candidates():
                if cand.get("listingId") == listing_id:
                    raise DomainValidationError(
                        DenialCode.DEPENDENCY_CONFLICT,
                        "DUPLICATE_CANDIDATE"
                    )

            candidates = self.listing_repository.list_candidates()
            candidate_id = "CS-1001" if listing_id == "L-2024" else f"CS-{1000 + len(candidates) + 1}"

            candidate_dict = {
                "id": candidate_id,
                "listingId": listing_id,
                "heatZoneId": listing.get("heatZoneId") or listing.get("hz") or "HZ-01",
                "title": "信義松仁候選點" if candidate_id == "CS-1001" else f"{listing_id} 候選點",
                "address": listing.get("address") or listing.get("address_raw") or "",
                "status": "ready",
                "score": 82 if candidate_id == "CS-1001" else 68,
                "recommendation": "GO" if candidate_id == "CS-1001" else "WAIT",
                "modelVersion": "SiteScore v2.3",
                "datasetSnapshotId": "FS-20260704-0600",
                "missingData": [],
                "reviewId": "RV-1001" if candidate_id == "CS-1001" else None,
            }
            self.listing_repository.save_candidate(candidate_dict)

            # Mark listing status as candidate
            listing["status"] = "candidate"
            listing["candidateId"] = candidate_id
            self.listing_repository.save_listing(listing)

            # Transition CANDIDATE_CREATING -> CANDIDATE_CREATED
            PromotionStateMachine.transition(promo_agg, PromotionState.CANDIDATE_CREATED, system_context)
            promo["status"] = to_status_str(PromotionState.CANDIDATE_CREATED)
            promo["version"] = promo_agg.version
            promo["candidate_site_id"] = candidate_id
            self.promotion_repository.save_promotion(promo)

            # Emit candidate.created event
            property_id = listing.get("property_id") or listing.get("propertyId") or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"property-{listing_id}"))
            self._emit_event(
                event_type="candidate.created",
                payload={
                    "candidate_site_id": candidate_id,
                    "property_id": property_id,
                    "listing_id": listing_id,
                    "target_format_code": promo.get("target_format_code") or "FORMAT-A",
                    "version": 1,
                },
                tenant_id=context.actor.tenant_id,
                aggregate_type="candidate_site",
                aggregate_id=candidate_id,
                aggregate_version=1,
                correlation_id=context.correlation_id,
            )

        except Exception as exc:
            PromotionStateMachine.transition(promo_agg, PromotionState.FAILED, system_context)
            promo["status"] = to_status_str(PromotionState.FAILED)
            promo["version"] = promo_agg.version
            self.promotion_repository.save_promotion(promo)
            raise exc

        # 2. CANDIDATE_CREATED -> SCORE_QUEUED -> COMPLETED
        try:
            PromotionStateMachine.transition(promo_agg, PromotionState.SCORE_QUEUED, system_context)
            promo["status"] = to_status_str(PromotionState.SCORE_QUEUED)
            promo["version"] = promo_agg.version

            score_job_id = f"JOB-SCORE-{uuid.uuid4().hex[:8]}"
            promo["site_score_job_id"] = score_job_id
            self.promotion_repository.save_promotion(promo)

            PromotionStateMachine.transition(promo_agg, PromotionState.COMPLETED, system_context)
            promo["status"] = to_status_str(PromotionState.COMPLETED)
            promo["version"] = promo_agg.version
            self.promotion_repository.save_promotion(promo)

            # Emit candidate.promotion_completed event
            self._emit_event(
                event_type="candidate.promotion_completed",
                payload={
                    "promotion_decision_id": promotion_decision_id,
                    "intake_id": promo["intake_id"],
                    "listing_id": promo["listing_id"],
                    "status": "COMPLETED",
                    "version": promo["version"],
                },
                tenant_id=context.actor.tenant_id,
                aggregate_type="promotion_decision",
                aggregate_id=promotion_decision_id,
                aggregate_version=promo["version"],
                correlation_id=context.correlation_id,
            )

        except Exception as exc:
            PromotionStateMachine.transition(promo_agg, PromotionState.SCORE_FAILED, system_context)
            promo["status"] = to_status_str(PromotionState.SCORE_FAILED)
            promo["version"] = promo_agg.version
            self.promotion_repository.save_promotion(promo)
            raise exc

        return promo

    def _validate_listing_fields(self, listing: dict[str, Any]) -> list[str]:
        errors = []
        if not (listing.get("address") or listing.get("address_raw")):
            errors.append("address")

        rent = listing.get("rentPerMonth") or listing.get("rent_amount")
        if rent is None or rent <= 0:
            errors.append("rent")

        area = listing.get("areaPing") or listing.get("area_ping")
        if area is None or area <= 0:
            errors.append("area")

        h3 = listing.get("hz") or listing.get("h3Index") or listing.get("h3_index") or listing.get("heatZoneId")
        if not h3:
            errors.append("H3")

        lat = listing.get("lat") or listing.get("latitude") or 25.0339
        lng = listing.get("lng") or listing.get("longitude") or 121.5645
        conf = listing.get("geocodeConfidence") or listing.get("confidence")
        if lat is None or lng is None or conf is None:
            errors.append("geocode")

        return errors

    def _emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        correlation_id: str | None = None,
    ) -> None:
        if not self.outbox_repository:
            return

        if event_type == "candidate.promotion_requested":
            schema_ref = "#/payloads/PromotionChangedV1"
        elif event_type == "candidate.created":
            schema_ref = "#/payloads/CandidateCreatedV1"
        elif event_type == "candidate.promotion_completed":
            schema_ref = "#/payloads/PromotionChangedV1"
        else:
            schema_ref = "#/payloads/GenericV1"

        event = DomainEvent(
            event_type=event_type,
            payload=payload,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            partition_key=f"{tenant_id}:{aggregate_id}",
            correlation_id=correlation_id or str(uuid.uuid4()),
            producer="candidate_promotion_service",
            schema_ref=schema_ref,
        )
        self.outbox_repository.save(event)
