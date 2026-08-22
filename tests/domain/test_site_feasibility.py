"""Domain unit & contract tests for ODayPlus Site Feasibility.

Verifies acceptance criteria for ODP-FEASIBILITY-001:
1. Model legal use, zoning, frontage, utilities, flood, loading, temporary stop and restrictions.
2. Return feasible, conditional, unknown-requires-survey or infeasible and fail closed before binding recommendation.
"""

import pytest

from modules.site_feasibility.application.service import SiteFeasibilityService
from modules.site_feasibility.domain.contracts import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    SiteFeasibilityDocument,
    validate_site_feasibility_document,
)
from modules.site_feasibility.domain.models import FeasibilityDecision


def test_missing_information_fails_closed_with_unknown_requires_survey():
    # If we have basic market context but no survey evidence about utilities/frontage, etc.
    # it must return UNKNOWN_REQUIRES_SURVEY
    service = SiteFeasibilityService()
    mock_market_context = {
        "context_id": "ctx-001",
        "identity": {
            "site_id": "SITE-001",
            "site_name": "Test Site",
            "primary_h3_index": "8928308280fffff",
            "latitude": 25.033,
            "longitude": 121.555,
            "h3_resolution": 9,
        }
    }
    
    doc = service.evaluate_feasibility(
        site_id="SITE-001",
        market_context=mock_market_context,
        surveys=[]
    )
    
    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert "utilities" in str(doc.decision.reasons).lower() or "survey" in str(doc.decision.reasons).lower()

def test_infeasible_site_due_to_zoning_or_flood():
    service = SiteFeasibilityService()
    mock_market_context = {
        "context_id": "ctx-001",
        "identity": {
            "site_id": "SITE-001",
            "site_name": "Test Site",
            "primary_h3_index": "8928308280fffff",
            "latitude": 25.033,
            "longitude": 121.555,
            "h3_resolution": 9,
            "metadata": {
                "zoning": "residential_strictly_no_commercial"
            }
        }
    }
    
    # Even with physical survey, the zoning forbids it
    mock_survey = {
        "survey_id": "surv-001",
        "tenant_id": "t1",
        "blob_id": "b1",
        "campaign_id": "c1",
        "target_entity_id": "SITE-001",
        "target_entity_kind": "SITE",
        "survey_type": "SITE_FEASIBILITY",
        "review_status": "APPROVED",
        "lifecycle_kind": "INITIAL_SURVEY",
        "submitter_id": "sub1",
        "surveyed_at": "2026-08-22T00:00:00Z",
        "submitted_at": "2026-08-22T00:00:00Z",
        "location": {"latitude": 25.033, "longitude": 121.555, "accuracy_meters": 5.0},
        "observation_id": "obs-001",
        "attributes": {
            "frontage_meters": 5.0,
            "utilities_power_capacity_amp": 100,
            "utilities_water_pressure_psi": 40,
            "flood_risk_level": "LOW",
            "loading_zone_available": True,
            "temporary_stop_allowed": True,
            "legal_use_restrictions": "NONE"
        }
    }
    
    doc = service.evaluate_feasibility(
        site_id="SITE-001",
        market_context=mock_market_context,
        surveys=[mock_survey]
    )
    
    assert doc.decision.recommendation == FeasibilityDecision.INFEASIBLE

def test_conditional_feasibility_due_to_low_power_capacity():
    service = SiteFeasibilityService()
    mock_market_context = {
        "context_id": "ctx-001",
        "identity": {
            "site_id": "SITE-001",
            "site_name": "Test Site",
            "primary_h3_index": "8928308280fffff",
            "latitude": 25.033,
            "longitude": 121.555,
            "h3_resolution": 9,
            "metadata": {
                "zoning": "commercial"
            }
        }
    }
    
    mock_survey = {
        "survey_id": "surv-001",
        "tenant_id": "t1",
        "blob_id": "b1",
        "campaign_id": "c1",
        "target_entity_id": "SITE-001",
        "target_entity_kind": "SITE",
        "survey_type": "SITE_FEASIBILITY",
        "review_status": "APPROVED",
        "lifecycle_kind": "INITIAL_SURVEY",
        "submitter_id": "sub1",
        "surveyed_at": "2026-08-22T00:00:00Z",
        "submitted_at": "2026-08-22T00:00:00Z",
        "location": {"latitude": 25.033, "longitude": 121.555, "accuracy_meters": 5.0},
        "observation_id": "obs-001",
        "attributes": {
            "frontage_meters": 5.0,
            "utilities_power_capacity_amp": 30, # Requires upgrade (e.g. standard might need 100)
            "utilities_water_pressure_psi": 40,
            "flood_risk_level": "LOW",
            "loading_zone_available": True,
            "temporary_stop_allowed": True,
            "legal_use_restrictions": "NONE"
        }
    }
    
    doc = service.evaluate_feasibility(
        site_id="SITE-001",
        market_context=mock_market_context,
        surveys=[mock_survey]
    )
    
    assert doc.decision.recommendation == FeasibilityDecision.CONDITIONAL

def test_fully_feasible_site():
    service = SiteFeasibilityService()
    mock_market_context = {
        "context_id": "ctx-001",
        "identity": {
            "site_id": "SITE-001",
            "site_name": "Test Site",
            "primary_h3_index": "8928308280fffff",
            "latitude": 25.033,
            "longitude": 121.555,
            "h3_resolution": 9,
            "metadata": {
                "zoning": "commercial"
            }
        }
    }
    
    mock_survey = {
        "survey_id": "surv-001",
        "tenant_id": "t1",
        "blob_id": "b1",
        "campaign_id": "c1",
        "target_entity_id": "SITE-001",
        "target_entity_kind": "SITE",
        "survey_type": "SITE_FEASIBILITY",
        "review_status": "APPROVED",
        "lifecycle_kind": "INITIAL_SURVEY",
        "submitter_id": "sub1",
        "surveyed_at": "2026-08-22T00:00:00Z",
        "submitted_at": "2026-08-22T00:00:00Z",
        "location": {"latitude": 25.033, "longitude": 121.555, "accuracy_meters": 5.0},
        "observation_id": "obs-001",
        "attributes": {
            "frontage_meters": 5.0,
            "utilities_power_capacity_amp": 150,
            "utilities_water_pressure_psi": 40,
            "flood_risk_level": "LOW",
            "loading_zone_available": True,
            "temporary_stop_allowed": True,
            "legal_use_restrictions": "NONE"
        }
    }
    
    doc = service.evaluate_feasibility(
        site_id="SITE-001",
        market_context=mock_market_context,
        surveys=[mock_survey]
    )
    
    assert doc.decision.recommendation == FeasibilityDecision.FEASIBLE


def _context(zoning: object = "commercial") -> dict[str, object]:
    return {
        "context_id": "ctx-001",
        "identity": {"site_id": "SITE-001", "metadata": {"zoning": zoning}},
    }


def _survey(**attributes: object) -> dict[str, object]:
    return {
        "survey_id": "surv-001",
        "target_entity_id": "SITE-001",
        "survey_type": "PHYSICAL_FEASIBILITY",
        "review_status": "APPROVED",
        "attributes": {
            "legal_use_restrictions": "NONE",
            "frontage_meters": 5.0,
            "utilities_power_capacity_amp": 100,
            "utilities_water_pressure_psi": 40,
            "flood_risk_level": "LOW",
            "loading_zone_available": True,
            "temporary_stop_allowed": True,
            **attributes,
        },
    }


def test_missing_required_evidence_fails_closed_even_when_power_is_present() -> None:
    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001",
        _context(),
        [_survey(
            legal_use_restrictions=None,
            frontage_meters=None,
            utilities_water_pressure_psi=None,
            flood_risk_level=None,
            loading_zone_available=None,
            temporary_stop_allowed=None,
        )],
    )

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    reasons = " ".join(doc.decision.reasons).lower()
    assert "legal use" in reasons
    assert "flood" in reasons


@pytest.mark.parametrize("flag", ["is_retracted", "is_superseded"])
def test_invalidated_survey_evidence_cannot_produce_feasible(flag: str) -> None:
    survey = _survey()
    survey[flag] = True

    doc = SiteFeasibilityService().evaluate_feasibility("SITE-001", _context(), [survey])

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert "survey" in " ".join(doc.decision.reasons).lower()


def test_rejected_survey_evidence_cannot_produce_feasible() -> None:
    survey = _survey()
    survey["review_status"] = "REJECTED"

    doc = SiteFeasibilityService().evaluate_feasibility("SITE-001", _context(), [survey])

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY


def test_frontage_is_modelled_as_a_non_binding_condition() -> None:
    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001", _context(), [_survey(frontage_meters=0)]
    )

    assert doc.decision.recommendation == FeasibilityDecision.CONDITIONAL
    assert "frontage" in " ".join(doc.decision.reasons).lower()


def test_unknown_zoning_is_not_assumed_to_be_permitted() -> None:
    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001", _context(zoning="unrecognised-zone"), [_survey()]
    )

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert "zoning" in " ".join(doc.decision.reasons).lower()


@pytest.mark.parametrize("wrong_site_zoning", ["commercial", "residential_strictly_no_commercial"])
def test_context_for_different_site_cannot_drive_the_feasibility_gate(
    wrong_site_zoning: str,
) -> None:
    context_document = {
        "document_id": "ctx-doc-001",
        "contexts": [
            {
                "context_id": "ctx-wrong-site",
                "identity": {
                    "site_id": "SITE-999",
                    "metadata": {"zoning": wrong_site_zoning},
                },
            }
        ],
    }

    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001", context_document, [_survey()]
    )

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert doc.metadata["binding_recommendation_allowed"] is False
    assert "zoning" in " ".join(doc.decision.reasons).lower()


def test_single_context_for_different_site_cannot_drive_the_feasibility_gate() -> None:
    context_for_other_site = _context()
    context_for_other_site["identity"] = {
        "site_id": "SITE-999",
        "metadata": {"zoning": "commercial"},
    }

    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001", context_for_other_site, [_survey()]
    )

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert doc.metadata["binding_recommendation_allowed"] is False
    assert "zoning" in " ".join(doc.decision.reasons).lower()


def test_contract_legal_values_are_handled_without_attribute_errors() -> None:
    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001",
        _context(zoning=None),
        [_survey(legal_use_restrictions=["NONE"])],
    )

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY


def test_multi_survey_aggregation_is_order_independent_and_fail_closed() -> None:
    low_power = _survey(utilities_power_capacity_amp=30)
    missing_power = _survey(utilities_power_capacity_amp=None)
    missing_power["survey_id"] = "surv-002"

    first = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001", _context(), [low_power, missing_power]
    )
    second = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001", _context(), [missing_power, low_power]
    )

    assert first.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert second.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert first.decision.reasons == second.decision.reasons


def test_object_shaped_site_restrictions_fail_closed() -> None:
    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001",
        _context(),
        [_survey(site_restrictions={"commercial_operation": "prohibited"})],
    )

    assert doc.decision.recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert doc.metadata["binding_recommendation_allowed"] is False
    assert "restriction" in " ".join(doc.decision.reasons).lower()


def test_all_acceptance_dimensions_and_contract_validation_are_covered() -> None:
    doc = SiteFeasibilityService().evaluate_feasibility(
        "SITE-001",
        _context(),
        [_survey(restrictions="NONE")],
    )

    assert doc.decision.recommendation == FeasibilityDecision.FEASIBLE
    assert doc.metadata["binding_recommendation_allowed"] is True
    validate_site_feasibility_document(doc)
    wire = doc.to_dict()
    assert wire["contract_id"] == CONTRACT_ID
    assert wire["contract_version"] == CONTRACT_VERSION
    assert SiteFeasibilityDocument.from_dict(wire).to_dict() == wire

    with pytest.raises(ValueError, match="contract_version"):
        validate_site_feasibility_document({**wire, "contract_version": "2.0.0"})
