"""Service for SiteScore v3."""

from typing import Any
from uuid import uuid4

from modules.site_economics.domain.models import EconomicsDecision
from modules.site_feasibility.domain.models import FeasibilityDecision
from modules.sitescore.v3.domain.contracts import SiteScoreV3Document
from modules.sitescore.v3.domain.models import (
    DecisionReadiness,
    ScoreAvailability,
    SiteScoreAssessment,
    SiteScoreComponents,
    SiteScoreDecision,
)


class SiteScoreV3Service:
    def evaluate(
        self, 
        site_id: str, 
        manifest_id: str, 
        market_context: dict[str, Any], 
        feasibility_doc: Any, 
        economics_doc: Any
    ) -> SiteScoreV3Document:
        
        reasons = []
        readiness = DecisionReadiness.READY
        
        # Check Provenance
        if market_context and market_context.get("manifest_id") and market_context.get("manifest_id") != manifest_id:
            readiness = DecisionReadiness.INCOMPLETE_FEASIBILITY
            reasons.append("Market context manifest mismatch.")
            
        if feasibility_doc and hasattr(feasibility_doc, "metadata") and feasibility_doc.metadata.get("manifest_id") and feasibility_doc.metadata.get("manifest_id") != manifest_id:
            readiness = DecisionReadiness.INCOMPLETE_FEASIBILITY
            reasons.append("Feasibility manifest mismatch.")
            
        if economics_doc and hasattr(economics_doc, "metadata") and economics_doc.metadata.get("manifest_id") and economics_doc.metadata.get("manifest_id") != manifest_id:
            readiness = DecisionReadiness.INCOMPLETE_ECONOMICS
            reasons.append("Economics manifest mismatch.")

        # Extract Decisions
        feasibility_decision = feasibility_doc.decision.recommendation if feasibility_doc and hasattr(feasibility_doc, "decision") else None
        
        economics_decision = None
        if economics_doc and hasattr(economics_doc, "decision") and hasattr(economics_doc.decision, "recommendation"):
            economics_decision = economics_doc.decision.recommendation
        elif economics_doc and hasattr(economics_doc, "summary") and hasattr(economics_doc.summary, "assessment"):
            economics_decision = economics_doc.summary.assessment.recommendation

        # Check Feasibility completeness
        if not feasibility_decision or feasibility_decision in [FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY, "UNKNOWN_REQUIRES_SURVEY"]:
            if readiness == DecisionReadiness.READY:
                readiness = DecisionReadiness.INCOMPLETE_FEASIBILITY
            reasons.append("Feasibility is unknown, requires survey.")
        elif feasibility_decision in [FeasibilityDecision.INFEASIBLE, "INFEASIBLE"]:
            reasons.append("Site is physically infeasible.")
            
        # Check Economics completeness
        if not economics_decision or economics_decision in ["UNKNOWN", "MISSING"]:
            if readiness == DecisionReadiness.READY:
                readiness = DecisionReadiness.INCOMPLETE_ECONOMICS
            reasons.append("Economics decision is missing.")
        elif economics_decision not in [EconomicsDecision.GO, EconomicsDecision.CONDITIONAL_GO, "GO", "CONDITIONAL_GO"]:
            reasons.append(f"Economics decision is not a GO: {economics_decision}.")

        if readiness != DecisionReadiness.READY:
            decision = SiteScoreDecision.INCOMPLETE
            availability = ScoreAvailability.UNAVAILABLE_MISSING_INPUT
            components = None
        else:
            availability = ScoreAvailability.AVAILABLE
            components = SiteScoreComponents(
                demand_score=float(market_context.get("demand_score", 0.0)),
                format_score=float(market_context.get("format_score", 0.0)),
                ramp_score=float(market_context.get("ramp_score", 0.0)),
                cannibalization_score=float(market_context.get("cannibalization_score", 0.0)),
                economics_score=float(market_context.get("economics_score", 0.0)),
                policy_score=float(market_context.get("policy_score", 0.0)),
            )
            
            # Known rejections are separated from missing inputs
            if feasibility_decision in [FeasibilityDecision.INFEASIBLE, "INFEASIBLE"] or economics_decision not in [EconomicsDecision.GO, EconomicsDecision.CONDITIONAL_GO, "GO", "CONDITIONAL_GO"]:
                decision = SiteScoreDecision.NO_GO
            else:
                decision = SiteScoreDecision.GO
            
        assessment = SiteScoreAssessment(
            availability=availability,
            readiness=readiness,
            decision=decision,
            components=components,
            reasons=tuple(reasons)
        )
        
        return SiteScoreV3Document(
            document_id=str(uuid4()),
            site_id=site_id,
            manifest_id=manifest_id,
            assessment=assessment,
        )
