from __future__ import annotations

import uuid
from collections.abc import Callable
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


# Geocode confidence is a property of the address, not of the listing record.
# A listing's own `confidence` is extraction confidence -- how sure the parser
# is about the rent, area and floor it read -- and the two are unrelated.
# Accepting it as a fallback spelling is what let the gate pass a listing whose
# geocode had failed: V1ListingRepositoryAdapter emits the address value under
# `geocode_confidence` and the listing value under `confidence`, so a 0.0
# geocode was masked by a 1.0 extraction confidence one key away.
_GEOCODE_CONFIDENCE_KEYS = ("geocodeConfidence", "geocode_confidence")


def geocode_confidence_of(listing: Any) -> float | None:
    """Return the address geocode confidence, or None when there is no geocode.

    Zero is absence, not a low-but-real confidence. `AddressLocation`
    defaults `geocode_confidence` to 0.0 and the persistence layer coerces a
    NULL column to 0.0, so nothing distinguishes "geocoder returned 0.0" from
    "never geocoded". `to_sitescore_model_row()` already reads it that way
    (``if not value.geocode_confidence``) and rejects the row; this gate has to
    agree, or it hands SiteScore a listing that SiteScore itself would refuse.

    A value that is present but not a number is also treated as absent: the
    caller cannot act on it, and guessing is the failure mode being removed.
    The first key that is present decides, rather than the first that looks
    usable -- the two spellings are aliases of one address field, so
    disagreeing values are a data fault, not a second opinion to fall back on.
    """
    for key in _GEOCODE_CONFIDENCE_KEYS:
        value = listing.get(key)
        if value is None:
            continue
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        return confidence if confidence > 0 else None
    return None


class PromotionService:
    """Manages the Intake-to-Listing-to-Candidate promotion saga."""

    def __init__(
        self,
        promotion_repository: Any,
        listing_repository: Any,
        intake_repository: Any,
        outbox_repository: Any = None,
        score_queue_hook: Callable[[], None] | None = None,
    ) -> None:
        self.promotion_repository = promotion_repository
        self.listing_repository = listing_repository
        self.intake_repository = intake_repository
        self.outbox_repository = outbox_repository
        self.score_queue_hook = score_queue_hook

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
            "proposer_subject_id": context.actor.actor_id,
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
            proposer_id=promo.get("proposer_subject_id") or promo.get("proposer"),
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

        # Re-run the candidate gate on the record actually being promoted,
        # before the saga starts moving.
        #
        # request_promotion() gates these fields at intake, but nothing
        # re-checked them here, and every accessor in the derivation below
        # supplied a plausible-looking default for a missing value: "HZ-01" for
        # an absent cell, "" for an absent address, 1.0 for an absent geocode
        # confidence, 75.0 when heat-zone scoring raised. A listing with no
        # address therefore reached score_site() carrying full confidence and a
        # passing demand signal, and could come back GO. ODP-BR-LST-001 is a
        # Hard Constraint: no address or failed geocode must not enter
        # SiteScore.
        #
        # It runs here rather than inside the saga because a gate failure is
        # not a scoring failure. The except branch below compensates a
        # candidate that already exists and leaves it for job.replay to restart
        # from SCORE_QUEUED; neither applies to a listing that should never
        # have been promoted. CANDIDATE_CREATING -> SCORE_FAILED is not a legal
        # transition either, so raising from inside would replace this denial
        # with a workflow-state error and strand the promotion mid-saga.
        promotion_address = self._assert_promotable(listing)

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

            # The gate already ran, above the saga. Every accessor below reads
            # a value the gate confirmed is present; none of them substitutes
            # one it does not have.
            if hasattr(listing, "get"):
                fit_score = listing.get("heat_zone_score") or listing.get("fitScore") or listing.get("fit_score")
                h3_val = listing.get("heatZoneId") or listing.get("hz") or listing.get("h3Index") or listing.get("h3_index")
                address_val = listing.get("address") or listing.get("address_raw")
                rent_val = listing.get("rentPerMonth") or listing.get("rent_amount")
                area_val = listing.get("areaPing") or listing.get("area_ping")
                # frontage is not a candidate-gate field; 0.0 remains its
                # documented absent value rather than a substituted one.
                frontage_val = listing.get("frontage_m") or listing.get("frontage") or 0.0
                conf_val = geocode_confidence_of(listing)
                title_val = listing.get("title") or f"{listing_id} 候選點"
                ds_id = listing.get("datasetSnapshotId") or listing.get("snapshot_id") or listing.get("dataset_snapshot_id")
            else:
                # Domain object Listing. The gate resolved and checked its
                # address already, so it is reused rather than re-fetched.
                address_obj = promotion_address
                h3_val = address_obj.h3_res_9
                address_val = address_obj.normalized_address
                rent_val = listing.rent_amount
                area_val = listing.area_ping
                frontage_val = listing.frontage_m
                conf_val = address_obj.geocode_confidence
                fit_score = getattr(listing, "heat_zone_score", None) or getattr(listing, "fit_score", None)
                title_val = f"{listing_id} 候選點"
                ds_id = getattr(listing, "snapshot_id", None)

            if fit_score is None:
                from modules.heatzone.domain.scoring import HeatZoneFeatureInput, score_heatzones
                # No fallback score. Swallowing the exception and substituting
                # 75.0 turned a scoring failure into a passing demand signal --
                # the same shape as the defaults above. A promotion that cannot
                # be scored must fail rather than proceed on a placeholder.
                hz_results = score_heatzones([HeatZoneFeatureInput(h3_index=h3_val)])
                if not hz_results:
                    raise DomainValidationError(
                        DenialCode.SOURCE_POLICY_DENIED,
                        f"Heat-zone scoring returned no result for cell {h3_val}"
                    )
                fit_score = hz_results[0].score

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

            prop_ent_id = (
                listing.get("property_id") or listing.get("propertyId") or listing.get("platform_property_id")
                if hasattr(listing, "get")
                else getattr(listing, "property_id", None) or getattr(listing, "platform_property_id", None)
            )
            list_obs_id = (
                listing.get("listing_obs_id") or listing.get("listingObsId") or listing.get("platform_observation_id")
                if hasattr(listing, "get")
                else getattr(listing, "listing_obs_id", None) or getattr(listing, "platform_observation_id", None)
            )
            bench_data = (
                listing.get("rent_benchmark") or {}
                if hasattr(listing, "get")
                else getattr(listing, "rent_benchmark", None) or {}
            )
            if not isinstance(bench_data, dict) and hasattr(bench_data, "to_dict"):
                bench_data = bench_data.to_dict()
            elif not isinstance(bench_data, dict):
                bench_data = {}

            bench_median = (
                listing.get("rent_benchmark_median") or bench_data.get("median_rent_per_ping")
                if hasattr(listing, "get")
                else getattr(listing, "rent_benchmark_median", None) or bench_data.get("median_rent_per_ping")
            )
            bench_p25 = (
                listing.get("rent_benchmark_p25") or bench_data.get("p25_rent_per_ping")
                if hasattr(listing, "get")
                else getattr(listing, "rent_benchmark_p25", None) or bench_data.get("p25_rent_per_ping")
            )
            bench_p75 = (
                listing.get("rent_benchmark_p75") or bench_data.get("p75_rent_per_ping")
                if hasattr(listing, "get")
                else getattr(listing, "rent_benchmark_p75", None) or bench_data.get("p75_rent_per_ping")
            )
            bench_count = (
                listing.get("rent_benchmark_sample_count") or bench_data.get("sample_count")
                if hasattr(listing, "get")
                else getattr(listing, "rent_benchmark_sample_count", None) or bench_data.get("sample_count")
            )
            bench_id = (
                listing.get("rent_benchmark_id") or bench_data.get("benchmark_id")
                if hasattr(listing, "get")
                else getattr(listing, "rent_benchmark_id", None) or bench_data.get("benchmark_id")
            )

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
            if prop_ent_id:
                candidate_dict["propertyId"] = prop_ent_id
            if list_obs_id:
                candidate_dict["listingObsId"] = list_obs_id
            if bench_median is not None:
                candidate_dict["rentBenchmarkMedian"] = float(bench_median)
            if bench_p25 is not None:
                candidate_dict["rentBenchmarkP25"] = float(bench_p25)
            if bench_p75 is not None:
                candidate_dict["rentBenchmarkP75"] = float(bench_p75)
            if bench_count is not None:
                candidate_dict["rentBenchmarkSampleCount"] = int(bench_count)
            if bench_id:
                candidate_dict["rentBenchmarkId"] = bench_id

            # Save candidate
            if hasattr(self.listing_repository, "save_candidate") and not hasattr(self.listing_repository, "_state"):
                from modules.listing.domain.models import CandidateSiteDraft
                from shared.domain.models import AddressLocation, CandidateSite
                address_id = (
                    listing.address_id
                    if not hasattr(listing, "get")
                    else listing.get("address_id")
                )
                orig_addr = (
                    self.listing_repository.get_address(address_id)
                    if address_id
                    and hasattr(self.listing_repository, "get_address")
                    else None
                )
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
                candidate_listing = (
                    self.listing_repository.get_domain_listing(listing_id)
                    if hasattr(self.listing_repository, "get_domain_listing")
                    else listing
                )
                if candidate_listing is None:
                    raise ValueError(f"Listing {listing_id} not found")
                draft = CandidateSiteDraft(
                    listing=candidate_listing,
                    address=orig_addr,
                    candidate_site=c_site,
                    heat_zone_id=h3_val,
                    status="CANDIDATE",
                    score=score_val,
                    recommendation=rec_val,
                    model_version=score_report.model_version,
                    dataset_snapshot_id=ds_snapshot_id,
                    review_id=candidate_dict["reviewId"],
                    property_entity_id=prop_ent_id,
                    listing_observation_id=list_obs_id,
                    rent_benchmark_median=float(bench_median) if bench_median is not None else None,
                    rent_benchmark_p25=float(bench_p25) if bench_p25 is not None else None,
                    rent_benchmark_p75=float(bench_p75) if bench_p75 is not None else None,
                    rent_benchmark_sample_count=int(bench_count) if bench_count is not None else None,
                    rent_benchmark_id=bench_id,
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

            if self.score_queue_hook:
                self.score_queue_hook()

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

    def _assert_promotable(self, listing: Any) -> Any | None:
        """Raise unless `listing` still satisfies the candidate gate.

        Returns the resolved `AddressLocation` when the listing is a domain
        object, so the caller derives the SiteScore input from the same address
        this checked, and `None` for a mapping, which carries its address
        fields inline.

        The two branches ask the same questions of two shapes:
        `_validate_listing_fields` reads a mapping, while a domain `Listing`
        keeps address, cell and geocode confidence on a separate record.
        """
        if hasattr(listing, "get"):
            errors = self._validate_listing_fields(listing)
            address_obj = None
        else:
            address_obj = (
                self.listing_repository.get_address(listing.address_id)
                if hasattr(self.listing_repository, "get_address")
                else None
            )
            errors = []
            if address_obj is None or not address_obj.normalized_address:
                errors.append("address")
            if address_obj is None or not address_obj.h3_res_9:
                errors.append("H3")
            if address_obj is None or not address_obj.geocode_confidence:
                # Same rule as geocode_confidence_of(): 0.0 is what
                # AddressLocation carries when nothing geocoded it, so it
                # cannot be read as a real measurement.
                errors.append("geocode")
            if not listing.rent_amount or listing.rent_amount <= 0:
                errors.append("rent")
            if not listing.area_ping or listing.area_ping <= 0:
                errors.append("area")

        if errors:
            raise DomainValidationError(
                DenialCode.SOURCE_POLICY_DENIED,
                f"Candidate gate failed at promotion: missing {', '.join(errors)}"
            )
        return address_obj

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

        # Geocode completeness is carried by the H3 cell (checked above) and
        # the geocode confidence, which is what the SiteScore input downstream
        # actually consumes -- SiteScoreFeatureInput takes heat_zone_id, not a
        # coordinate pair.
        #
        # This used to read:
        #
        #     lat = listing.get("lat") or listing.get("latitude") or 25.0339
        #     lng = listing.get("lng") or listing.get("longitude") or 121.5645
        #     conf = listing.get("geocodeConfidence") or listing.get("confidence")
        #     if lat is None or lng is None or conf is None:
        #
        # which rejected almost nothing. The coordinate fallback made
        # `lat is None` unreachable, so the condition reduced to `conf is None`;
        # and `conf` fell through to the listing's own extraction confidence, so
        # a failed geocode still produced a number. Coordinates are deliberately
        # not restored as a gate field -- AddressLocation defaults
        # latitude/longitude to 0.0, the promotion payload does not carry them,
        # and SiteScoreFeatureInput takes heat_zone_id -- so promoting them to a
        # real requirement belongs with the address contract, not here.
        if geocode_confidence_of(listing) is None:
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
