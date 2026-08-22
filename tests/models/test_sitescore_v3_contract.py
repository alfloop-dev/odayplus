"""Domain unit and contract tests for ODayPlus SiteScore v3."""

from unittest.mock import MagicMock

import pytest

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


def _decision_doc(manifest_id: str | None, recommendation: object) -> MagicMock:
    document = MagicMock()
    document.metadata = {"manifest_id": manifest_id} if manifest_id is not None else {}
    document.decision.recommendation = recommendation
    return document


def _market_context(manifest_id: str | None, *, with_scores: bool = False) -> dict[str, object]:
    context: dict[str, object] = {}
    if manifest_id is not None:
        context["manifest_id"] = manifest_id
    if with_scores:
        context.update(
            {
                "demand_score": 0.8,
                "format_score": 0.9,
                "ramp_score": 0.85,
                "cannibalization_score": 0.1,
                "economics_score": 0.95,
                "policy_score": 1.0,
            }
        )
    return context


def test_sitescore_v3_incomplete_feasibility_prevents_binding_go():
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("mf-001", FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY)
    economics_doc = _decision_doc("mf-001", EconomicsDecision.GO)

    result = service.evaluate(
        site_id="SITE-001",
        manifest_id="mf-001",
        market_context=_market_context("mf-001"),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_FEASIBILITY
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert "Feasibility is unknown, requires survey." in result.assessment.reasons
    assert result.assessment.components is None
    assert validate_sitescore_v3_document(result)


def test_sitescore_v3_incomplete_economics_prevents_binding_go():
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("mf-002", FeasibilityDecision.FEASIBLE)
    economics_doc = _decision_doc("mf-002", "UNKNOWN")

    result = service.evaluate(
        site_id="SITE-002",
        manifest_id="mf-002",
        market_context=_market_context("mf-002"),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_ECONOMICS
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert "Economics decision is missing." in result.assessment.reasons


def test_sitescore_v3_complete_inputs_emit_binding_go():
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("mf-003", FeasibilityDecision.FEASIBLE)
    economics_doc = _decision_doc("mf-003", EconomicsDecision.GO)

    result = service.evaluate(
        site_id="SITE-003",
        manifest_id="mf-003",
        market_context=_market_context("mf-003", with_scores=True),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.READY
    assert result.assessment.availability == ScoreAvailability.AVAILABLE
    assert result.assessment.decision == SiteScoreDecision.GO
    assert result.assessment.components is not None
    assert result.assessment.components.demand_score == 0.8
    assert result.manifest_id == "mf-003"
    assert result.contract_id == CONTRACT_ID
    assert result.contract_version == CONTRACT_VERSION


def test_sitescore_v3_missing_market_manifest_prevents_binding_go():
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("mf-006", FeasibilityDecision.FEASIBLE)
    economics_doc = _decision_doc("mf-006", EconomicsDecision.GO)

    result = service.evaluate(
        site_id="SITE-006",
        manifest_id="mf-006",
        market_context={},
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_FEASIBILITY
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert "Market context manifest is missing." in result.assessment.reasons


@pytest.mark.parametrize(
    ("document_name", "feasibility_manifest_id", "economics_manifest_id"),
    [
        ("feasibility", None, "mf-007"),
        ("economics", "mf-008", None),
    ],
)
def test_sitescore_v3_missing_input_manifest_prevents_binding_go(
    document_name: str,
    feasibility_manifest_id: str | None,
    economics_manifest_id: str | None,
):
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc(feasibility_manifest_id, FeasibilityDecision.FEASIBLE)
    economics_doc = _decision_doc(economics_manifest_id, EconomicsDecision.GO)
    manifest_id = "mf-007" if document_name == "feasibility" else "mf-008"

    result = service.evaluate(
        site_id="SITE-007",
        manifest_id=manifest_id,
        market_context=_market_context(manifest_id, with_scores=True),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert any(
        f"{document_name.capitalize()} manifest is missing." in reason
        for reason in result.assessment.reasons
    )


def test_sitescore_v3_provenance_mismatch_prevents_readiness():
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("stale-001", FeasibilityDecision.FEASIBLE)
    economics_doc = _decision_doc("mf-004", EconomicsDecision.GO)

    result = service.evaluate(
        site_id="SITE-004",
        manifest_id="mf-004",
        market_context=_market_context("mf-004"),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_FEASIBILITY
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert any("Feasibility manifest mismatch" in reason for reason in result.assessment.reasons)


def test_sitescore_v3_conditional_feasibility_is_not_binding_go():
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("mf-009", FeasibilityDecision.CONDITIONAL)
    economics_doc = _decision_doc("mf-009", EconomicsDecision.GO)

    result = service.evaluate(
        site_id="SITE-009",
        manifest_id="mf-009",
        market_context=_market_context("mf-009", with_scores=True),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_FEASIBILITY
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert "Feasibility is conditional and requires resolution." in result.assessment.reasons


@pytest.mark.parametrize(
    "economics_recommendation",
    [
        EconomicsDecision.CONDITIONAL_GO,
        EconomicsDecision.INVESTIGATE,
    ],
)
def test_sitescore_v3_nonbinding_economics_is_not_binding_go(economics_recommendation):
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("mf-010", FeasibilityDecision.FEASIBLE)
    economics_doc = _decision_doc("mf-010", economics_recommendation)

    result = service.evaluate(
        site_id="SITE-010",
        manifest_id="mf-010",
        market_context=_market_context("mf-010", with_scores=True),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.INCOMPLETE_ECONOMICS
    assert result.assessment.decision == SiteScoreDecision.INCOMPLETE
    assert result.assessment.availability == ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision != SiteScoreDecision.GO


def test_sitescore_v3_known_infeasible_emits_no_go():
    service = SiteScoreV3Service()
    feasibility_doc = _decision_doc("mf-005", FeasibilityDecision.INFEASIBLE)
    economics_doc = _decision_doc("mf-005", EconomicsDecision.REJECT)

    result = service.evaluate(
        site_id="SITE-005",
        manifest_id="mf-005",
        market_context=_market_context("mf-005"),
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert result.assessment.readiness == DecisionReadiness.READY
    assert result.assessment.availability == ScoreAvailability.AVAILABLE
    assert result.assessment.decision == SiteScoreDecision.NO_GO
    assert "Site is physically infeasible." in result.assessment.reasons
