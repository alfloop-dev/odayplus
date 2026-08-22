"""Site Feasibility Service."""

from typing import Any, Mapping
from uuid import uuid4

from modules.site_feasibility.domain.models import FeasibilityAssessment, FeasibilityDecision
from modules.site_feasibility.domain.contracts import SiteFeasibilityDocument


class SiteFeasibilityService:
    def evaluate_feasibility(
        self,
        site_id: str,
        market_context: Mapping[str, Any],
        surveys: list[Mapping[str, Any]],
    ) -> SiteFeasibilityDocument:
        reasons = []
        recommendation = FeasibilityDecision.FEASIBLE

        # 1. Market Context - Check Zoning
        identity = market_context.get("identity", {})
        metadata = identity.get("metadata", {})
        zoning = metadata.get("zoning", "").lower()

        if "residential_strictly_no_commercial" in zoning or "no_commercial" in zoning:
            reasons.append(f"Zoning forbids commercial use: {zoning}")
            recommendation = FeasibilityDecision.INFEASIBLE

        # 2. Surveys check
        if not surveys:
            reasons.append("Missing physical survey for utilities, frontage, etc.")
            if recommendation != FeasibilityDecision.INFEASIBLE:
                recommendation = FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
        else:
            # We assume the most recent/relevant survey is provided, or we can aggregate
            for survey in surveys:
                attrs = survey.get("attributes", {})
                
                # Check Legal use restrictions
                legal_use = attrs.get("legal_use_restrictions", "NONE").upper()
                if legal_use != "NONE":
                    reasons.append(f"Legal use restriction: {legal_use}")
                    recommendation = FeasibilityDecision.INFEASIBLE
                    
                # Check Flood Risk
                flood = attrs.get("flood_risk_level", "LOW").upper()
                if flood == "HIGH" or flood == "CRITICAL":
                    reasons.append(f"High flood risk: {flood}")
                    recommendation = FeasibilityDecision.INFEASIBLE

                # Check utilities
                power = attrs.get("utilities_power_capacity_amp")
                if power is not None:
                    if power < 50:
                        reasons.append(f"Low power capacity: {power}A")
                        if recommendation == FeasibilityDecision.FEASIBLE:
                            recommendation = FeasibilityDecision.CONDITIONAL
                else:
                    reasons.append("Power capacity missing from survey")
                    if recommendation not in (FeasibilityDecision.INFEASIBLE, FeasibilityDecision.CONDITIONAL):
                        recommendation = FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY

                # Wait, what if there's no loading zone?
                loading = attrs.get("loading_zone_available")
                temp_stop = attrs.get("temporary_stop_allowed")
                if loading is False and temp_stop is False:
                    reasons.append("No loading zone and temporary stop is not allowed")
                    if recommendation == FeasibilityDecision.FEASIBLE:
                        recommendation = FeasibilityDecision.CONDITIONAL

        doc = SiteFeasibilityDocument(
            document_id=str(uuid4()),
            site_id=site_id,
            decision=FeasibilityAssessment(
                recommendation=recommendation,
                reasons=reasons,
            ),
            metadata={"source_market_context_id": market_context.get("context_id")}
        )
        return doc
