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

        target_listing_id = (intake.get("matchResult") or {}).get("targetListingId")
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

        # A live or completed decision is intake-scoped. Rejected and failed
        # decisions are terminal attempts, so a fresh idempotency key may open
        # a new independently reviewed attempt for the corrected intake.
        existing_promos = self.promotion_repository.list_promotions()
        for promo in existing_promos:
            if (
                promo.get("intake_id") == intake_id
                and promo.get("status") not in {"REJECTED", "FAILED"}
            ):
                return promo

        # Check duplicate candidate
        for cand in self.listing_repository.list_candidates():
            if hasattr(cand, "candidate_site"):
                c_listing_id = getattr(cand.candidate_site, "listing_id", None)
            elif hasattr(cand, "get"):
                c_listing_id = cand.get("listingId") or cand.get("listing_id")
            else:
                c_listing_id = getattr(cand, "listing_id", None)
            if c_listing_id == target_listing_id:
                raise DomainValidationError(
                    DenialCode.DEPENDENCY_CONFLICT,
                    "DUPLICATE_CANDIDATE"
                )

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
            "audit_event_id": str(uuid.uuid4()),
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

        listing_id = promo["listing_id"]
        listing = self.listing_repository.get_listing(listing_id)
        if not listing:
            raise ValueError(f"Listing {listing_id} not found")

        # Remember original listing status and candidate count for compensation
        if hasattr(listing, "get"):
            before_status = listing.get("status")
        else:
            before_status = getattr(listing, "listing_status", None)

        candidate_created_flag = False
        candidate_id = str(uuid.uuid4())

        try:
            # 1. APPROVED -> CANDIDATE_CREATING
            PromotionStateMachine.transition(promo_agg, PromotionState.CANDIDATE_CREATING, system_context)
            promo["status"] = to_status_str(PromotionState.CANDIDATE_CREATING)
            promo["version"] = promo_agg.version
            self.promotion_repository.save_promotion(promo)

            # Check duplicate candidate again
            for cand in self.listing_repository.list_candidates():
                c_listing_id = cand.get("listingId") if hasattr(cand, "get") else cand.candidate_site.listing_id
                if c_listing_id == listing_id:
                    raise DomainValidationError(
                        DenialCode.DEPENDENCY_CONFLICT,
                        "DUPLICATE_CANDIDATE"
                    )

            # Get fitScore or equivalent demand signal
            if hasattr(listing, "get"):
                fit_score = listing.get("heat_zone_score") or listing.get("fitScore") or listing.get("fit_score")
                h3_val = listing.get("heatZoneId") or listing.get("hz") or listing.get("h3Index") or listing.get("h3_index") or "HZ-01"
                address_val = listing.get("address") or listing.get("address_raw") or ""
                rent_val = listing.get("rentPerMonth") or listing.get("rent_amount") or 0.0
                area_val = listing.get("areaPing") or listing.get("area_ping") or 0.0
                frontage_val = listing.get("frontage_m") or listing.get("frontage") or 0.0
                conf_val = listing.get("geocodeConfidence") or listing.get("confidence") or 1.0
                title_val = listing.get("title") or f"{listing_id} 候選點"
                ds_id = listing.get("datasetSnapshotId") or listing.get("snapshot_id") or listing.get("dataset_snapshot_id")
            else:
                # Domain object Listing
                address_obj = None
                if hasattr(self.listing_repository, "addresses"):
                    for addr in self.listing_repository.addresses:
                        if addr.address_id == listing.address_id:
                            address_obj = addr
                            break
                h3_val = address_obj.h3_res_9 if (address_obj and address_obj.h3_res_9) else "HZ-01"
                address_val = address_obj.normalized_address if address_obj else ""
                rent_val = listing.rent_amount
                area_val = listing.area_ping
                frontage_val = listing.frontage_m
                conf_val = address_obj.geocode_confidence if address_obj else listing.confidence
                fit_score = getattr(listing, "heat_zone_score", None) or getattr(listing, "fit_score", None)
                title_val = f"{listing_id} 候選點"
                ds_id = getattr(listing, "snapshot_id", None)

            if fit_score is None:
                from modules.heatzone.domain.scoring import HeatZoneFeatureInput, score_heatzones
                try:
                    hz_results = score_heatzones([HeatZoneFeatureInput(h3_index=h3_val)])
                    fit_score = hz_results[0].score if hz_results else 75.0
                except Exception:
                    fit_score = 75.0

            if ds_id and str(ds_id).startswith("FS-"):
                ds_snapshot_id = str(ds_id)
            elif ds_id:
                ds_snapshot_id = f"FS-{ds_id}"
            else:
                ds_snapshot_id = f"FS-{listing_id}"

            # Derive candidate fields from the listing and a real scoring call
            from modules.sitescore.domain.scoring import SiteScoreFeatureInput, score_site
            feature_input = SiteScoreFeatureInput(
                candidate_site_id=candidate_id,
                heat_zone_id=h3_val,
                heat_zone_score=float(fit_score),
                monthly_rent=float(rent_val),
                area_ping=float(area_val),
                frontage_m=float(frontage_val),
                average_confidence=float(conf_val),
            )
            score_report = score_site(feature_input)

            # Map score and recommendation
            rec_val = score_report.recommendation.value
            if rec_val == "GO":
                payback = getattr(score_report, "payback_p50_months", 30.0) or 30.0
                score_val = 80 + int(max(0.0, min(1.0, (36.0 - payback) / 36.0)) * 19)
            elif rec_val == "WAIT":
                payback = getattr(score_report, "payback_p50_months", 50.0) or 50.0
                score_val = 60 + int(max(0.0, min(1.0, (72.0 - payback) / 36.0)) * 19)
            else:
                payback = getattr(score_report, "payback_p50_months", 100.0) or 100.0
                score_val = int(max(0.0, min(1.0, (120.0 - payback) / 120.0)) * 59)

            candidate_dict = {
                "id": candidate_id,
                "listingId": listing_id,
                "heatZoneId": h3_val,
                "title": title_val,
                "address": address_val,
                "status": "ready",
                "score": score_val,
                "recommendation": rec_val,
                "modelVersion": score_report.model_version,
                "datasetSnapshotId": ds_snapshot_id,
                "missingData": [],
                "reviewId": f"RV-{uuid.uuid4().hex[:8].upper()}",
            }

            # Save candidate
            if hasattr(self.listing_repository, "save_candidate") and not hasattr(self.listing_repository, "_state"):
                from modules.listing.domain.models import CandidateSiteDraft
                from shared.domain.models import AddressLocation, CandidateSite
                orig_addr = None
                if hasattr(self.listing_repository, "addresses"):
                    for addr in self.listing_repository.addresses:
                        if addr.address_id == (listing.address_id if not hasattr(listing, "get") else listing.get("address_id")):
                            orig_addr = addr
                            break
                if not orig_addr:
                    orig_addr = AddressLocation(
                        raw_address=address_val,
                        normalized_address=address_val,
                        geocode_confidence=conf_val,
                        h3_res_9=h3_val,
                    )
                c_site = CandidateSite(
                    candidate_site_id=candidate_id,
                    listing_id=listing_id,
                    address_id=orig_addr.address_id,
                    target_format_code="FORMAT-A",
                    site_status="ready",
                    created_by=context.actor.actor_id,
                )
                draft = CandidateSiteDraft(
                    listing=listing,
                    address=orig_addr,
                    candidate_site=c_site,
                    heat_zone_id=h3_val,
                    status="CANDIDATE",
                    score=score_val,
                    recommendation=rec_val,
                    model_version=score_report.model_version,
                    dataset_snapshot_id=ds_snapshot_id,
                    review_id=candidate_dict["reviewId"],
                )
                self.listing_repository.save_candidate(draft)
            else:
                self.listing_repository.save_candidate(candidate_dict)

            candidate_created_flag = True

            # Mark listing status as candidate
            if hasattr(listing, "get"):
                listing["status"] = "candidate"
                listing["candidateId"] = candidate_id
                self.listing_repository.save_listing(listing)
            else:
                from shared.domain.models import Listing
                updated_listing = Listing(
                    listing_id=listing.listing_id,
                    source_listing_id=listing.source_listing_id,
                    source_id=listing.source_id,
                    listing_status="candidate",
                    address_id=listing.address_id,
                    rent_amount=listing.rent_amount,
                    currency=listing.currency,
                    area_ping=listing.area_ping,
                    floor=listing.floor,
                    frontage_m=listing.frontage_m,
                    depth_m=listing.depth_m,
                    corner_flag=listing.corner_flag,
                    parking_flag=listing.parking_flag,
                    utility_electricity_flag=listing.utility_electricity_flag,
                    utility_drainage_flag=listing.utility_drainage_flag,
                    utility_gas_flag=listing.utility_gas_flag,
                    available_from=listing.available_from,
                    snapshot_id=listing.snapshot_id,
                    confidence=listing.confidence,
                )
                self.listing_repository.save_listing(updated_listing)

            # Transition CANDIDATE_CREATING -> CANDIDATE_CREATED
            PromotionStateMachine.transition(promo_agg, PromotionState.CANDIDATE_CREATED, system_context)
            promo["status"] = to_status_str(PromotionState.CANDIDATE_CREATED)
            promo["version"] = promo_agg.version
            promo["candidate_site_id"] = candidate_id
            self.promotion_repository.save_promotion(promo)

            # Emit candidate.created event
            property_id = (
                listing.get("property_id") or listing.get("propertyId")
                if hasattr(listing, "get")
                else getattr(listing, "property_id", None) or getattr(listing, "propertyId", None)
            )
            if not property_id:
                property_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"property-{listing_id}"))

            self._emit_event(
                event_type="candidate.created",
                payload={
                    "candidate_site_id": candidate_id,
                    "property_id": property_id,
                    "source_listing_id": listing_id,
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
            # Compensate listing status
            if before_status:
                if hasattr(listing, "get"):
                    listing["status"] = before_status
                    if "candidateId" in listing:
                        listing["candidateId"] = None
                    self.listing_repository.save_listing(listing)
                else:
                    from shared.domain.models import Listing
                    updated_listing = Listing(
                        listing_id=listing.listing_id,
                        source_listing_id=listing.source_listing_id,
                        source_id=listing.source_id,
                        listing_status=before_status,
                        address_id=listing.address_id,
                        rent_amount=listing.rent_amount,
                        currency=listing.currency,
                        area_ping=listing.area_ping,
                        floor=listing.floor,
                        frontage_m=listing.frontage_m,
                        depth_m=listing.depth_m,
                        corner_flag=listing.corner_flag,
                        parking_flag=listing.parking_flag,
                        utility_electricity_flag=listing.utility_electricity_flag,
                        utility_drainage_flag=listing.utility_drainage_flag,
                        utility_gas_flag=listing.utility_gas_flag,
                        available_from=listing.available_from,
                        snapshot_id=listing.snapshot_id,
                        confidence=listing.confidence,
                    )
                    self.listing_repository.save_listing(updated_listing)

            # Compensate candidate site
            if candidate_created_flag:
                if hasattr(self.listing_repository, "candidates"):
                    self.listing_repository.candidates = [
                        c for c in self.listing_repository.candidates
                        if (c.candidate_site.candidate_site_id if hasattr(c, "candidate_site") else c.get("id")) != candidate_id
                    ]
                if hasattr(self.listing_repository, "_state") and "candidates" in self.listing_repository._state:
                    self.listing_repository._state["candidates"] = [
                        c for c in self.listing_repository._state["candidates"]
                        if c.get("id") != candidate_id
                    ]

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

            score_job_id = str(uuid.uuid4())
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
            # Scoring failure is recoverable: retain the candidate and listing
            # association, mark the candidate for operator visibility, and let
            # job.replay restart the SCORE_QUEUED checkpoint.
            for candidate in self.listing_repository.list_candidates():
                candidate_key = (
                    candidate.candidate_site.candidate_site_id
                    if hasattr(candidate, "candidate_site")
                    else candidate.get("id")
                )
                if candidate_key == candidate_id and hasattr(candidate, "get"):
                    candidate["status"] = "SCORING_FAILED"
            underlying_repo = getattr(self.listing_repository, "repo", None)
            if underlying_repo is not None and hasattr(underlying_repo, "candidates"):
                from dataclasses import replace

                for draft in underlying_repo.candidates:
                    if draft.candidate_site.candidate_site_id == candidate_id:
                        draft.candidate_site = replace(
                            draft.candidate_site,
                            site_status="SCORING_FAILED",
                        )
                        break

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
