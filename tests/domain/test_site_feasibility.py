"""Domain unit & contract tests for ODayPlus Site Feasibility.

Verifies acceptance criteria for ODP-FEASIBILITY-001:
1. Model legal use, zoning, frontage, utilities, flood, loading, temporary stop and restrictions.
2. Return feasible, conditional, unknown-requires-survey or infeasible and fail closed before binding recommendation.
"""

import pytest

from modules.site_feasibility.domain.models import FeasibilityDecision, FeasibilityAssessment
from modules.site_feasibility.application.service import SiteFeasibilityService

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

