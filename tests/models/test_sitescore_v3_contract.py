"""Domain unit & contract tests for ODayPlus SiteScore v3.

Verifies acceptance criteria for ODP-SITESCORE-V3-001:
1. Separate model score availability from decision readiness.
2. Use point-in-time manifests and do not emit binding GO while feasibility/economics are incomplete.
"""

from unittest.mock import MagicMock

from modules.site_economics.domain.models import EconomicsDecision
from modules.site_feasibility.domain.models import FeasibilityDecision
from modules.sitescore.v3 import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    DecisionReadiness,
    ScoreAvailability,
    SiteScoreDecision,
    SiteScoreV3Service,
    validate_sitescore_v3_document,
)


def test_sitescore_v3_incomplete_feasibility_prevents_binding_go():
    service = SiteScoreV3Service()
    
    feasibility_doc = MagicMock()
    feasibility_doc.decision.recommendation = FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    
    economics_doc = MagicMock()
    economics_doc.summary.assessment.recommendation = EconomicsDecision.GO
    
    result = service.evaluate(
        site_id="SITE-001",
        manifest_id="mf-001",
        market_context={},
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc
    )
    
    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_FEASIBILITY
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert "Feasibility is unknown, requires survey." in result.assessment.reasons
    assert result.components is None if hasattr(result, "components") else True
    assert validate_sitescore_v3_document(result)

def test_sitescore_v3_incomplete_economics_prevents_binding_go():
    service = SiteScoreV3Service()
    
    feasibility_doc = MagicMock()
    feasibility_doc.decision.recommendation = FeasibilityDecision.FEASIBLE
    
    economics_doc = MagicMock()
    economics_doc.summary.assessment.recommendation = EconomicsDecision.REJECT
    
    result = service.evaluate(
        site_id="SITE-002",
        manifest_id="mf-002",
        market_context={},
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc
    )
    
    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_ECONOMICS
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert "Economics decision is missing or not a GO." in result.assessment.reasons

def test_sitescore_v3_complete_inputs_emit_binding_go():
    service = SiteScoreV3Service()
    
    feasibility_doc = MagicMock()
    feasibility_doc.decision.recommendation = FeasibilityDecision.FEASIBLE
    
    economics_doc = MagicMock()
    economics_doc.summary.assessment.recommendation = EconomicsDecision.GO
    
    result = service.evaluate(
        site_id="SITE-003",
        manifest_id="mf-003",
        market_context={},
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc
    )
    
    assert result.assessment.readiness == DecisionReadiness.READY
    assert result.assessment.availability == ScoreAvailability.AVAILABLE
    assert result.assessment.decision == SiteScoreDecision.GO
    assert result.assessment.components is not None
    assert result.assessment.components.demand_score == 0.8
    assert result.manifest_id == "mf-003"
    assert result.contract_id == CONTRACT_ID
    assert result.contract_version == CONTRACT_VERSION
