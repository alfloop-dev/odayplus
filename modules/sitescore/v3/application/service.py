"""Service for SiteScore v3."""

from typing import Any
from uuid import uuid4

from modules.sitescore.v3.domain.models import (
    ScoreAvailability, DecisionReadiness, SiteScoreDecision, SiteScoreComponents, SiteScoreAssessment
)
from modules.sitescore.v3.domain.contracts import SiteScoreV3Document
from modules.site_feasibility.domain.models import FeasibilityDecision
from modules.site_economics.domain.models import EconomicsDecision


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
        
        # Check Feasibility
        feasibility_decision = feasibility_doc.decision.recommendation if feasibility_doc and hasattr(feasibility_doc, "decision") else None
        
        if not feasibility_decision or feasibility_decision in [FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY, "UNKNOWN_REQUIRES_SURVEY"]:
            readiness = DecisionReadiness.INCOMPLETE_FEASIBILITY
            reasons.append("Feasibility is unknown, requires survey.")
        elif feasibility_decision in [FeasibilityDecision.INFEASIBLE, "INFEASIBLE"]:
            readiness = DecisionReadiness.INCOMPLETE_FEASIBILITY
            reasons.append("Site is physically infeasible.")
            
        # Check Economics
        economics_decision = economics_doc.summary.assessment.recommendation if economics_doc and hasattr(economics_doc, "summary") else None
        if not economics_decision or economics_decision not in [EconomicsDecision.GO, EconomicsDecision.CONDITIONAL_GO, "GO", "CONDITIONAL_GO"]:
            if readiness == DecisionReadiness.READY:
                readiness = DecisionReadiness.INCOMPLETE_ECONOMICS
            reasons.append("Economics decision is missing or not a GO.")

        if readiness != DecisionReadiness.READY:
            decision = SiteScoreDecision.INCOMPLETE
            availability = ScoreAvailability.UNAVAILABLE_MISSING_INPUT
            components = None
        else:
            decision = SiteScoreDecision.GO
            availability = ScoreAvailability.AVAILABLE
            components = SiteScoreComponents(
                demand_score=0.8,
                format_score=0.9,
                ramp_score=0.85,
                cannibalization_score=0.1,
                economics_score=0.95,
                policy_score=1.0,
            )
            
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
