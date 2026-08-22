"""Contract and integration tests for Market Intelligence BFF API and Product Authorization.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.

Acceptance Criteria:
1. Expose market cells, site context, compare, evidence, coverage, data gaps and acquisition plans.
2. Apply product authorization and return readiness/missingness explicitly.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from apps.api.app.routes.market_intelligence import create_market_intelligence_router
from modules.external_data.application.market_data_facade import MarketDataFacade
from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    InMemoryDataPlatformTransport,
)
from modules.market_intelligence_api import (
    ALLOWED_MARKET_INTELLIGENCE_ROLES,
    CONTRACT_CATEGORY,
    CONTRACT_ID,
    CONTRACT_VERSION,
    REQUIRED_CONTRACTS,
    CandidateCellSummary,
    CandidateSiteSummary,
    CoverageFilter,
    DataPlatformMarketIntelligenceRepository,
    MarketIntelligenceNotFoundError,
    MarketIntelligenceService,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    SiteMarketContext,
)
from shared.auth import (
    Role,
)
from shared.auth.engine import AuthorizationEngine

OPENAPI_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "schemas"
    / "openapi"
    / "market_intelligence"
    / "openapi.json"
)

TENANT_ALPHA = "00000000-0000-0000-0000-000000000001"
TENANT_BETA = "00000000-0000-0000-0000-000000000002"

HEADERS_EXPANSION_ALPHA = {
    "x-subject-id": "00000000-0000-0000-0000-000000000101",
    "x-tenant-id": TENANT_ALPHA,
    "x-roles": "expansion_user,site_reviewer",
    "x-operator-role": "expansion-manager",
}

HEADERS_EXPANSION_BETA = {
    "x-subject-id": "00000000-0000-0000-0000-000000000201",
    "x-tenant-id": TENANT_BETA,
    "x-roles": "expansion_user,site_reviewer",
    "x-operator-role": "expansion-manager",
}

HEADERS_ADMIN_ALPHA = {
    "x-subject-id": "00000000-0000-0000-0000-000000000001",
    "x-tenant-id": TENANT_ALPHA,
    "x-roles": "platform_admin",
    "x-operator-role": "admin",
}

HEADERS_UNAUTHORIZED_ROLE = {
    "x-subject-id": "00000000-0000-0000-0000-000000000999",
    "x-tenant-id": TENANT_ALPHA,
    "x-roles": "guest_viewer",
}


@pytest.fixture
def sample_site_context_payload() -> dict[str, Any]:
    return {
        "document_id": "smc-doc-001",
        "generated_at": "2026-08-14T00:00:00Z",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "tenant_id": TENANT_ALPHA,
        "source_support": {
            "source_dataset_ids": ["ds-1"],
            "observation_count": 100,
            "sample_count": 100,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
            "freshness_state": "fresh",
            "age_seconds": 3600,
            "negative_evidence_valid": True,
        },
        "contexts": [
            {
                "context_id": "smc-ctx-001",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "identity": {
                    "site_id": "site-taipei-001",
                    "site_name": "Taipei Xinyi Flagship #1",
                    "latitude": 25.033,
                    "longitude": 121.565,
                    "primary_h3_index": "8928308280fffff",
                    "h3_resolution": 9,
                    "district": "Xinyi",
                    "county": "Taipei City",
                },
                "catchment": {
                    "catchment_id": "cat-001",
                    "travel_mode": "pedestrian",
                    "cutoff_seconds": 600,
                    "routing_engine": "valhalla",
                    "graph_version": "v1.0",
                    "status": "available",
                    "h3_resolution": 9,
                    "total_cells_count": 1,
                    "geom": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [121.5, 25.0],
                                [121.6, 25.0],
                                [121.6, 25.1],
                                [121.5, 25.1],
                                [121.5, 25.0],
                            ]
                        ],
                    },
                },
                "demand": {
                    "status": "available",
                    "total_population": 50000.0,
                    "male_population": 24000.0,
                    "female_population": 26000.0,
                    "household_count": 18000.0,
                    "density_per_sq_km": 1000.0,
                    "daytime_population_ratio": 1.2,
                    "source_support": {
                        "observation_count": 50000,
                        "sample_count": 50000,
                        "source_dataset_ids": ["ds-demand"],
                        "uncertainty_pct": 5.0,
                    },
                },
                "poi": {
                    "status": "available",
                    "total_poi_count": 500,
                    "convenience_stores_count": 20,
                    "commercial_centers_count": 5,
                    "transit_stations_count": 4,
                    "schools_count": 6,
                    "hospitals_count": 2,
                    "density_per_sq_km": 50.0,
                },
                "competitor": {
                    "status": "available",
                    "total_competitors": 10,
                    "active_competitors": 8,
                    "competitor_density_per_sq_km": 2.0,
                    "brands_present": ["BrandA", "BrandB"],
                    "stores_by_brand": {"BrandA": 5, "BrandB": 3},
                    "stores_by_category": {"laundromat": 8},
                    "source_support": {
                        "observation_count": 8,
                        "sample_count": 8,
                        "source_dataset_ids": ["ds-competitor"],
                        "uncertainty_pct": 10.0,
                        # Not part of canonical SourceSupportSummary; this
                        # must not leak into the BFF evidence response.
                        "negative_evidence_valid": True,
                    },
                },
                "rent": {
                    "status": "available",
                    "mean_rent_per_ping": 2500.0,
                    "median_rent_per_ping": 2400.0,
                    "p25_rent_per_ping": 2000.0,
                    "p75_rent_per_ping": 3000.0,
                    "sample_count": 30,
                    "confidence_pct": 95.0,
                },
                "listing": {
                    "status": "available",
                    "active_listings_count": 15,
                    "mean_asking_rent_per_ping": 2600.0,
                    "median_asking_rent_per_ping": 2500.0,
                    "average_area_ping": 30.0,
                },
                "mobility": {
                    "status": "available",
                    "stay_duration_minutes_mean": 45.0,
                    "unique_visitors_daily": 12000.0,
                    "activity_population": 15000,
                },
                "traffic": {
                    "status": "available",
                    "daily_traffic_volume": 35000.0,
                    "hourly_volume_vph": 1200,
                },
                "event": {
                    "status": "available",
                    "active_events_count": 3,
                    "events": [],
                },
                "coverage": {
                    "overall_readiness": "ready",
                    "domain_coverage": {
                        "DEMOGRAPHICS": "complete",
                        "RENT": "complete",
                        "POI": "complete",
                        "COMPETITOR": "complete",
                    },
                    "domain_freshness": {
                        "DEMOGRAPHICS": "fresh",
                        "TRANSPORT": "stale",
                        "EVENTS": "unknown",
                    },
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-1"],
                    "observation_count": 100,
                    "sample_count": 100,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                    "freshness_state": "fresh",
                    "age_seconds": 3600,
                    "negative_evidence_valid": True,
                },
                "component_manifest_refs": [
                    {
                        "component_id": "comp-geo-001",
                        "component_kind": "GEOGRAPHY",
                        "contract_id": "emgi.geo.v1",
                        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    }
                ],
            },
            {
                "context_id": "smc-ctx-002",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "identity": {
                    "site_id": "site-taipei-002",
                    "site_name": "Taipei Daan Candidate #2",
                    "latitude": 25.026,
                    "longitude": 121.543,
                    "primary_h3_index": "8928308281fffff",
                    "h3_resolution": 9,
                    "district": "Daan",
                    "county": "Taipei City",
                },
                "catchment": {
                    "catchment_id": "cat-002",
                    "travel_mode": "pedestrian",
                    "cutoff_seconds": 600,
                    "routing_engine": "valhalla",
                    "graph_version": "v1.0",
                    "status": "available",
                    "h3_resolution": 9,
                    "total_cells_count": 1,
                    "geom": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [121.5, 25.0],
                                [121.6, 25.0],
                                [121.6, 25.1],
                                [121.5, 25.1],
                                [121.5, 25.0],
                            ]
                        ],
                    },
                },
                "demand": {
                    "status": "available",
                    "total_population": 35000.0,
                    "male_population": 17000.0,
                    "female_population": 18000.0,
                    "household_count": 12000.0,
                    "density_per_sq_km": 800.0,
                    "daytime_population_ratio": 1.1,
                },
                "poi": {
                    "status": "available",
                    "total_poi_count": 300,
                    "convenience_stores_count": 12,
                    "commercial_centers_count": 2,
                    "transit_stations_count": 2,
                    "schools_count": 4,
                    "hospitals_count": 1,
                    "density_per_sq_km": 30.0,
                },
                "competitor": {
                    "status": "available",
                    "total_competitors": 4,
                    "active_competitors": 3,
                    "competitor_density_per_sq_km": 0.8,
                    "brands_present": ["BrandC"],
                    "stores_by_brand": {"BrandC": 3},
                    "stores_by_category": {"laundromat": 3},
                },
                "rent": {
                    "status": "unavailable",
                    "mean_rent_per_ping": None,
                    "median_rent_per_ping": None,
                    "p25_rent_per_ping": None,
                    "p75_rent_per_ping": None,
                    "sample_count": 0,
                    "confidence_pct": None,
                    "unavailable_reason": "NO_RENT_TRANSACTIONS_IN_RADIUS",
                },
                "listing": {
                    "status": "available",
                    "active_listings_count": 5,
                    "mean_asking_rent_per_ping": 2100.0,
                    "average_area_ping": 25.0,
                },
                "mobility": {
                    "status": "available",
                    "stay_duration_minutes_mean": 30.0,
                    "unique_visitors_daily": 8000.0,
                    "activity_population": 10000,
                },
                "traffic": {
                    "status": "available",
                    "daily_traffic_volume": 20000.0,
                    "hourly_volume_vph": 800,
                },
                "event": {
                    "status": "available",
                    "active_events_count": 1,
                    "events": [],
                },
                "coverage": {
                    "overall_readiness": "usable_with_gaps",
                    "domain_coverage": {"DEMOGRAPHICS": "complete", "RENT": "missing"},
                    "domain_freshness": {
                        "DEMOGRAPHICS": "fresh",
                        "TRANSPORT": "fresh",
                        "EVENTS": "stale",
                    },
                    "has_gaps": True,
                    "readiness_reasons": [
                        {
                            "code": "MISSING_RENT_OBSERVATIONS",
                            "detail": "No commercial rent transactions observed within catchment",
                            "severity": "degrading",
                            "domain": "rent",
                        }
                    ],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-1"],
                    "observation_count": 50,
                    "sample_count": 50,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                    "freshness_state": "fresh",
                    "age_seconds": 7200,
                    "negative_evidence_valid": False,
                },
                "component_manifest_refs": [],
            },
        ],
    }


@pytest.fixture
def sample_market_cell_payload() -> dict[str, Any]:
    return {
        "profile_id": "mcp-doc-001",
        "generated_at": "2026-08-14T00:00:00Z",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "tenant_id": TENANT_ALPHA,
        "h3_resolution": 9,
        "source_support": {
            "source_dataset_ids": ["ds-cell"],
            "observation_count": 50,
            "sample_count": 50,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
            "freshness_state": "fresh",
            "age_seconds": 1800,
            "negative_evidence_valid": True,
        },
        "cells": [
            {
                "cell_id": "8928308280fffff",
                "h3_index": "8928308280fffff",
                "h3_resolution": 9,
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "admin_code": "63000010",
                "county": "Taipei City",
                "district": "Xinyi",
                "demographics": {
                    "total_population": 12000.0,
                    "household_count": 4500.0,
                    "density_per_sq_km": 4000.0,
                },
                "competitors": {
                    "total_competitors": 3,
                    "active_competitors": 2,
                    "brands_present": ["BrandA"],
                    "stores_by_brand": {"BrandA": 2},
                    "stores_by_category": {"laundromat": 2},
                },
                "rent": {
                    "mean_rent_per_ping": 2800.0,
                    "median_rent_per_ping": 2700.0,
                    "sample_count": 10,
                    "confidence_pct": 95.0,
                },
                "listings": {
                    "active_listings_count": 8,
                },
                "points_of_interest": {
                    "total_poi_count": 150,
                },
                "mobility": {
                    "total_foot_traffic": 45000.0,
                    "activity_population": 3000,
                },
                "events": {
                    "active_events_count": 2,
                },
                "coverage": {
                    "overall_readiness": "ready",
                    "domain_coverage": {"GEOGRAPHY": "complete"},
                    "domain_freshness": {
                        "DEMOGRAPHICS": "fresh",
                        "TRANSPORT": "stale",
                        "EVENTS": "unknown",
                    },
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-cell"],
                    "observation_count": 50,
                    "sample_count": 50,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                    "freshness_state": "fresh",
                    "age_seconds": 1800,
                    "negative_evidence_valid": True,
                },
            }
        ],
    }


@pytest.fixture
def sample_coverage_surface_payload() -> dict[str, Any]:
    return {
        "surface_id": "cov-surface-001",
        "tenant_id": TENANT_ALPHA,
        "domain": "GEOGRAPHY",
        "dataset_ids": ["ds-geo-1"],
        "generated_at": "2026-08-14T00:00:00Z",
        "product_version": "0.4.1",
        "readiness": "ready",
        "readiness_reasons": [],
        "scope_principal_id": "principal-sys",
        "spatial_grain": "h3_res9",
        "temporal_grain": "monthly",
        "coverage_percentage": 92.5,
        "overall_state": "complete",
        "cells": [
            {
                "cell_id": "8928308280fffff",
                "h3_index": "8928308280fffff",
                "state": "complete",
                "readiness": "ready",
                "is_complete": True,
                "negative_evidence_valid": True,
                "observed_count": 150,
                "expected_count": 160,
                "freshness_state": "fresh",
                "admin_code": "63000010",
                "reasons": [],
            }
        ],
        "state_breakdown": {
            "complete": 10,
            "empty": 0,
            "partial": 2,
            "saturated": 0,
            "source_error": 0,
            "truncated": 0,
            "unknown": 0,
        },
        "freshness": {
            "evaluated_at": "2026-08-14T00:00:00Z",
            "state": "fresh",
            "stale_cell_count": 0,
            "unknown_cell_count": 0,
        },
        "search_completeness": {
            "complete": 10,
            "partial": 2,
            "refinement_required": False,
            "saturated": 0,
            "saturated_partition_keys": [],
            "source_error": 0,
            "truncated": 0,
            "truncated_partition_keys": [],
            "unknown": 0,
        },
        "blockers": [],
    }


@pytest.fixture
def sample_data_gap_payload() -> dict[str, Any]:
    return {
        "contract_version": "emgi.data-gap.v1",
        "tenant_id": TENANT_ALPHA,
        "generated_at": "2026-08-14T00:00:00Z",
        "surface_id": "cov-surface-001",
        "gaps": [
            {
                "blocking": False,
                "dataset_id": "ds-rent-1",
                "detail": "Sample count below statistical threshold in sub-district",
                "detected_at": "2026-08-14T00:00:00Z",
                "domain": "rent",
                "gap_id": "gap-rent-xinyi-01",
                "gap_kind": "missingness",
                "reason_code": "LOW_SAMPLE_COUNT",
                "remediation": "field_survey_expansion",
                "scope_principal_id": "principal-sys",
                "cell_id": "8928308281fffff",
            }
        ],
    }


@pytest.fixture
def sample_acquisition_plan_payload() -> dict[str, Any]:
    return {
        "plan_id": "plan-acq-001",
        "tenant_id": TENANT_ALPHA,
        "site_context_id": "site-taipei-002",
        "coverage_surface_id": "cov-surface-001",
        "status": "proposed",
        "plan_version": 1,
        "effective_as_of": "2026-08-14T00:00:00Z",
        "knowledge_as_of": "2026-08-14T00:00:00Z",
        "gaps": [
            {
                "gap_id": "gap-rent-xinyi-01",
                "domain": "rent",
                "measure": "mean_rent_per_ping",
                "priority_rank": 1,
                "current_uncertainty_pct": 65.0,
                "expected_uncertainty_reduction_pct": 40.0,
                "decision_sensitivity": 0.85,
                "estimated_cost_units": 500.0,
                "estimated_latency_hours": 48.0,
                "survey_effort_hours": 6.0,
                "quota_units": 10.0,
                "rationale": "Conduct targeted broker survey for commercial leases",
                "recommended_source_ids": ["field_survey_v1"],
            }
        ],
        "experiments": [
            {
                "baseline_uncertainty_pct": 65.0,
                "expected_uplift_pct": 40.0,
                "experiment_id": "exp-001",
                "gap_ids": ["gap-rent-xinyi-01"],
                "hypothesis": "Field survey reduces rental estimate uncertainty",
                "max_cost_units": 500.0,
                "max_latency_hours": 48.0,
                "max_quota_units": 10.0,
                "paid_source": True,
                "prior_value_evidence": True,
                "sample_size": 15,
                "scope": "site",
                "source_id": "field_survey_v1",
                "status": "planned",
                "success_criteria": ["uncertainty_below_30"],
                "survey_effort_hours": 6.0,
            }
        ],
        "policy": {"max_budget_units": 2000.0},
        "metadata": {"created_by": "expansion_team"},
    }


@pytest.fixture
def test_setup(
    sample_site_context_payload: dict[str, Any],
    sample_market_cell_payload: dict[str, Any],
    sample_coverage_surface_payload: dict[str, Any],
    sample_data_gap_payload: dict[str, Any],
    sample_acquisition_plan_payload: dict[str, Any],
) -> tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport]:
    transport = InMemoryDataPlatformTransport()
    transport.store_document(
        "emgi.site-market-context.v1", "smc-doc-001", sample_site_context_payload
    )
    transport.store_document(
        "emgi.market-cell-profile.v1", "mcp-doc-001", sample_market_cell_payload
    )
    transport.store_document(
        "emgi.coverage-surface.v1", "cov-surface-001", sample_coverage_surface_payload
    )
    transport.store_document("emgi.data-gap.v1", "gap-doc-001", sample_data_gap_payload)
    transport.store_document(
        "emgi.data-acquisition-plan.v1", "plan-acq-001", sample_acquisition_plan_payload
    )

    client = DataPlatformClient(transport=transport)
    auth_engine = AuthorizationEngine()
    facade = MarketDataFacade(client=client, auth_engine=auth_engine)
    service = MarketIntelligenceService(facade=facade, auth_engine=auth_engine)

    app = FastAPI(title="Market Intelligence Test App")
    router = create_market_intelligence_router(service=service)
    app.include_router(router, prefix="/api/v1")

    test_client = TestClient(app)
    return test_client, service, transport


# ===========================================================================
# 1. Contract Constants and OpenAPI Verification Tests
# ===========================================================================


def test_contract_metadata_and_invariants() -> None:
    assert CONTRACT_ID == "odayplus.market-intelligence-api.v2"
    assert CONTRACT_VERSION == "2.0.0"
    assert CONTRACT_CATEGORY == "bff_api"
    assert "odayplus.market-data-facade.v2" in REQUIRED_CONTRACTS
    assert "emgi.site-market-context.v1" in REQUIRED_CONTRACTS
    assert "emgi.coverage-surface.v1" in REQUIRED_CONTRACTS
    assert "emgi.data-acquisition-plan.v1" in REQUIRED_CONTRACTS
    assert Role.EXPANSION_USER in ALLOWED_MARKET_INTELLIGENCE_ROLES
    assert Role.PLATFORM_ADMIN in ALLOWED_MARKET_INTELLIGENCE_ROLES


def test_openapi_specification_file_validity() -> None:
    assert OPENAPI_PATH.exists(), f"OpenAPI spec missing at {OPENAPI_PATH}"
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.0.3"
    assert spec["info"]["version"] == "2.0.0"
    paths = spec["paths"]
    assert "/market-intelligence/health" in paths
    assert "/market-intelligence/diagnostics" in paths
    assert "/market-intelligence/cells/{cell_id}" in paths
    assert "/market-intelligence/cells" in paths
    assert "/market-intelligence/sites/{site_id}/context" in paths
    assert "/market-intelligence/sites/context/batch" in paths
    assert "/market-intelligence/compare" in paths
    assert "/market-intelligence/evidence/{site_id}" in paths
    assert "/market-intelligence/evidence/cells/{cell_id}" in paths
    assert "/market-intelligence/coverage" in paths
    assert "/market-intelligence/data-gaps" in paths
    assert "/market-intelligence/data-gaps/{gap_id}" in paths
    assert "/market-intelligence/acquisition-plans" in paths
    assert "/market-intelligence/acquisition-plans/{plan_id}" in paths


def test_openapi_documents_fail_closed_health_and_unavailable_routes() -> None:
    """The canonical contract must describe the unbound production runtime."""
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    paths = spec["paths"]
    health_response_schema = spec["paths"]["/market-intelligence/health"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert health_response_schema == {
        "$ref": "#/components/schemas/MarketIntelligenceHealth"
    }
    health_schema = spec["components"]["schemas"]["MarketIntelligenceHealth"]

    assert set(health_schema["properties"]["status"]["enum"]) == {
        "healthy",
        "degraded",
        "unavailable",
    }
    assert "version" not in health_schema["required"]
    for field in ("ready", "available", "reasonCode", "missing"):
        assert field in health_schema["properties"]

    unavailable_health = {
        "status": "unavailable",
        "ready": False,
        "available": False,
        "service": "market_intelligence_bff",
        "contract": CONTRACT_ID,
        "reasonCode": "MARKET_INTELLIGENCE_PRODUCTION_BINDING_REQUIRED",
        "missing": ["data_platform_binding"],
    }
    assert not list(Draft202012Validator(health_schema).iter_errors(unavailable_health))

    for path, path_item in paths.items():
        if path == "/market-intelligence/health":
            continue
        for operation in path_item.values():
            assert operation["responses"]["503"] == {
                "$ref": "#/components/responses/ServiceUnavailableError"
            }, f"missing fail-closed response for {path}"

    unavailable_response = spec["components"]["responses"]["ServiceUnavailableError"]
    assert (
        unavailable_response["content"]["application/json"]["schema"]["properties"]["detail"][
            "$ref"
        ]
        == "#/components/schemas/MarketIntelligenceUnavailableError"
    )


# ===========================================================================
# 2. Product Authorization and Tenant Isolation Tests
# ===========================================================================


def test_unauthenticated_request_rejected_with_401(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get("/api/v1/market-intelligence/sites/site-taipei-001/context")
    assert resp.status_code in {401, 403}


def test_unauthorized_role_rejected_with_403(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/sites/site-taipei-001/context",
        headers=HEADERS_UNAUTHORIZED_ROLE,
    )
    assert resp.status_code == 403


def test_diagnostics_requires_market_intelligence_authorization(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/diagnostics",
        headers=HEADERS_UNAUTHORIZED_ROLE,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "role_unauthorized"


def test_cross_tenant_access_denied_without_admin(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    headers_beta = {
        "x-subject-id": "00000000-0000-0000-0000-000000000201",
        "x-tenant-id": TENANT_BETA,
        "x-roles": "expansion_user",
    }
    resp = client.get(
        "/api/v1/market-intelligence/sites/site-taipei-001/context",
        headers=headers_beta,
    )
    assert resp.status_code in {403, 404}


def test_admin_cross_tenant_access_allowed(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/sites/site-taipei-001/context",
        headers=HEADERS_ADMIN_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["identity"]["site_id"] == "site-taipei-001"


# ===========================================================================
# 3. Site Market Context Endpoints
# ===========================================================================


def test_get_site_market_context_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/sites/site-taipei-001/context",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["identity"]["site_id"] == "site-taipei-001"
    assert data["identity"]["district"] == "Xinyi"
    assert data["demand"]["total_population"] == 50000.0
    assert data["competitor"]["active_competitors"] == 8
    assert data["rent"]["mean_rent_per_ping"] == 2500.0
    assert data["poi"]["total_poi_count"] == 500
    assert data["coverage"]["overall_readiness"] == "ready"


def test_unscoped_site_context_is_hidden_from_every_tenant(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, transport = test_setup
    raw = transport.fetch_document("emgi.site-market-context.v1", document_id="smc-doc-001")
    assert raw is not None
    raw.pop("tenant_id", None)

    for headers in (HEADERS_EXPANSION_ALPHA, HEADERS_EXPANSION_BETA):
        response = client.get(
            "/api/v1/market-intelligence/sites/site-taipei-001/context",
            headers=headers,
        )
        assert response.status_code == 404


def test_get_site_market_context_not_found(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/sites/nonexistent-site/context",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 404


def test_batch_get_site_contexts(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.post(
        "/api/v1/market-intelligence/sites/context/batch",
        json={"site_ids": ["site-taipei-001", "site-taipei-002", "site-missing"]},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert len(data["items"]) == 2


# ===========================================================================
# 4. Market Cell Endpoints
# ===========================================================================


def test_get_market_cell_profile_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/cells/8928308280fffff",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cell_id"] == "8928308280fffff"
    assert data["demographics"]["total_population"] == 12000.0
    assert data["competitors"]["active_competitors"] == 2


def test_unscoped_market_cell_profile_is_hidden_from_every_tenant(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, transport = test_setup
    raw = transport.fetch_document("emgi.market-cell-profile.v1", document_id="mcp-doc-001")
    assert raw is not None
    raw.pop("tenant_id", None)

    for headers in (HEADERS_EXPANSION_ALPHA, HEADERS_EXPANSION_BETA):
        response = client.get(
            "/api/v1/market-intelligence/cells/8928308280fffff",
            headers=headers,
        )
        assert response.status_code == 404


def test_list_market_cells_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/cells",
        params={"cell_ids": "8928308280fffff"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["cell_id"] == "8928308280fffff"


# ===========================================================================
# 5. Candidate Compare Endpoints & Explicit Missingness
# ===========================================================================


def test_candidate_compare_post_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.post(
        "/api/v1/market-intelligence/compare",
        json={
            "site_ids": ["site-taipei-001", "site-taipei-002"],
            "period_grain": "MONTHLY",
            "period_key": "2026-08",
        },
        headers={**HEADERS_EXPANSION_ALPHA, "Idempotency-Key": f"idem-cmp-{uuid4()}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_candidates"] == 2
    assert "domain_comparisons" in data
    assert "demand" in data["domain_comparisons"]
    assert "competitor" in data["domain_comparisons"]
    assert "rent" in data["domain_comparisons"]

    # Invariant: site-taipei-001 has population 50000, site-taipei-002 has 35000
    assert data["domain_comparisons"]["demand"]["best_candidate_id"] == "site-taipei-001"
    assert data["domain_comparisons"]["demand"]["values_by_candidate"]["site-taipei-001"] == 50000.0

    # Invariant: site-taipei-002 has 3 active competitors, site-taipei-001 has 8 -> 002 is least competitive
    assert data["domain_comparisons"]["competitor"]["best_candidate_id"] == "site-taipei-002"

    # Invariant: Explicit Missingness on Rent!
    # site-taipei-002 has NO rent transactions. Value MUST NOT be rendered as 0.0!
    assert data["domain_comparisons"]["rent"]["values_by_candidate"]["site-taipei-002"] is None
    assert "site-taipei-002" in data["domain_comparisons"]["rent"]["missing_candidate_ids"]
    assert "rent" in data["missing_domains_by_candidate"]["site-taipei-002"]

    first_candidate = next(c for c in data["candidates"] if c["site_id"] == "site-taipei-001")
    assert first_candidate["traffic"]["hourly_volume_vph"] == 1200
    assert "daily_traffic_volume" not in first_candidate["traffic"]
    assert first_candidate["mobility"]["activity_population"] == 15000
    assert first_candidate["mobility"]["resident_population"] is None
    assert "unique_visitors_daily" not in first_candidate["mobility"]


def test_candidate_compare_get_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/compare",
        params={"site_ids": "site-taipei-001,site-taipei-002"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_candidates"] == 2


def test_candidate_compare_market_cell_uses_only_canonical_components(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/compare",
        params={"cell_ids": "8928308280fffff"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    candidate = resp.json()["candidates"][0]
    assert candidate["competitor"]["status"] == "available"
    assert "poi" in candidate["missing_domains"]
    assert "listing" in candidate["missing_domains"]
    assert "event" in candidate["missing_domains"]
    assert candidate["mobility"]["activity_population"] == 3000
    assert "total_foot_traffic" not in candidate["mobility"]


def test_canonical_mobility_fields_do_not_fallback_to_other_populations(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    _, _, transport = test_setup
    raw_context = transport.fetch_document("emgi.site-market-context.v1", document_id="smc-doc-001")
    assert raw_context is not None
    context = SiteMarketContext.from_dict(raw_context["contexts"][0])
    zero_summary = CandidateSiteSummary.from_site_context(
        replace(
            context,
            mobility=replace(
                context.mobility,
                activity_population=0.0,
                resident_population=111.0,
                visitor_population=222.0,
            ),
        )
    )
    assert zero_summary.activity_population == 0.0

    summary = CandidateSiteSummary.from_site_context(
        replace(
            context,
            mobility=replace(
                context.mobility,
                activity_population=None,
                resident_population=111.0,
                visitor_population=222.0,
            ),
        )
    )
    assert summary.activity_population is None
    assert summary.resident_population == 111.0
    assert summary.visitor_population == 222.0


def test_zero_competitors_remain_an_observed_cell_domain(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    _, _, transport = test_setup
    raw_cell = transport.fetch_document("emgi.market-cell-profile.v1", document_id="mcp-doc-001")
    assert raw_cell is not None
    cell = MarketCellProfile.from_dict(raw_cell["cells"][0])
    empty_competitor_cell = replace(
        cell,
        competitors=replace(cell.competitors, active_competitors=0, total_competitors=0),
    )
    summary = CandidateCellSummary.from_cell_profile(empty_competitor_cell)
    assert summary.competitor_status == "available"
    assert summary.active_competitors == 0
    assert "competitor" not in summary.missing_domains


# ===========================================================================
# 6. Evidence and Lineage Endpoints
# ===========================================================================


def test_get_site_evidence_chain_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/evidence/site-taipei-001",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["site_id"] == "site-taipei-001"
    assert "domains" in data
    assert "demand" in data["domains"]
    assert data["domains"]["demand"]["observation_count"] == 50000
    assert data["domains"]["demand"]["sources"] == ["ds-demand"]
    assert data["domains"]["demand"]["freshness_state"] == "fresh"
    assert data["domains"]["demand"]["confidence_pct"] == 95.0
    assert "age_seconds" not in data["domains"]["demand"]
    assert "competitor" in data["domains"]
    assert data["domains"]["competitor"]["sources"] == ["ds-competitor"]
    assert data["domains"]["competitor"]["negative_evidence_valid"] is None
    assert data["domains"]["event"]["negative_evidence_valid"] is None
    assert data["domains"]["traffic"]["freshness_state"] == "stale"
    assert data["domains"]["event"]["freshness_state"] == "unknown"
    assert data["overall_confidence_pct"] is None
    assert len(data["component_manifest_refs"]) >= 1


def test_get_cell_evidence_chain_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/evidence/cells/8928308280fffff",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cell_id"] == "8928308280fffff"
    assert "demand" in data["domains"]


# ===========================================================================
# 7. Coverage Surface and Data Gaps Endpoints
# ===========================================================================


def test_get_coverage_surface_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params={"surface_id": "cov-surface-001"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["surface_id"] == "cov-surface-001"
    assert data["readiness"] == "ready"
    assert len(data["cells"]) == 1


def test_unscoped_coverage_surface_is_hidden_from_every_tenant(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, transport = test_setup
    raw = transport.fetch_document("emgi.coverage-surface.v1", document_id="cov-surface-001")
    assert raw is not None
    raw.pop("tenant_id", None)

    for headers in (HEADERS_EXPANSION_ALPHA, HEADERS_EXPANSION_BETA):
        response = client.get(
            "/api/v1/market-intelligence/coverage",
            params={"surface_id": "cov-surface-001"},
            headers=headers,
        )
        assert response.status_code == 404


# ===========================================================================
# 7b. Coverage Query Filter Semantics
# ===========================================================================
#
# admin_code, h3_index, business_date, readiness and state name CoverageCell
# fields, so they select which cells a surface reports. A surface must never
# be served for a query whose predicates none of its cells satisfy.


def _coverage_cell(
    cell_id: str,
    *,
    state: str = "complete",
    readiness: str = "ready",
    admin_code: str = "63000010",
    business_date: str | None = None,
) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_id": cell_id,
        "h3_index": cell_id,
        "state": state,
        "readiness": readiness,
        "is_complete": state == "complete",
        "negative_evidence_valid": True,
        "observed_count": 10,
        "expected_count": 10,
        "freshness_state": "fresh",
        "admin_code": admin_code,
        "reasons": [],
    }
    if business_date is not None:
        cell["business_date"] = business_date
    return cell


def _coverage_surface_with(
    base: dict[str, Any],
    *,
    surface_id: str,
    cells: list[dict[str, Any]],
    tenant_id: str = TENANT_ALPHA,
    readiness: str = "ready",
) -> dict[str, Any]:
    payload = json.loads(json.dumps(base))
    payload["surface_id"] = surface_id
    payload["tenant_id"] = tenant_id
    payload["readiness"] = readiness
    payload["cells"] = cells
    return payload


class _ParamsIgnoringTransport(InMemoryDataPlatformTransport):
    """Transport that returns the stored document regardless of query filters.

    A remote transport may ignore filters it does not implement, so the BFF
    must enforce coverage query semantics itself rather than trusting them
    to have been applied upstream.
    """

    def fetch_document(
        self,
        contract_id: str,
        *,
        document_id: str | None = None,
        params: Any = None,
    ) -> Any:
        return super().fetch_document(contract_id, document_id=document_id, params=None)


@pytest.mark.parametrize(
    "query",
    [
        {"surface_id": "cov-surface-001", "readiness": "blocked"},
        {"surface_id": "cov-surface-001", "admin_code": "does-not-exist"},
        {"surface_id": "cov-surface-001", "h3_index": "does-not-exist"},
        {"surface_id": "cov-surface-001", "state": "blocked"},
        {"surface_id": "cov-surface-001", "business_date": "1999-01-01"},
        {"readiness": "blocked"},
        {"admin_code": "does-not-exist"},
    ],
)
def test_coverage_query_filters_are_applied_not_ignored(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
    query: dict[str, str],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params=query,
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 404, (
        f"query {query} must not be served the ready surface: {resp.json()}"
    )


def test_coverage_query_filters_that_match_return_the_cell(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params={
            "surface_id": "cov-surface-001",
            "admin_code": "63000010",
            "h3_index": "8928308280fffff",
            "readiness": "ready",
            "state": "complete",
        },
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["surface_id"] == "cov-surface-001"
    assert [cell["cell_id"] for cell in data["cells"]] == ["8928308280fffff"]


def test_coverage_filter_selects_the_surface_whose_cells_match(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
    sample_coverage_surface_payload: dict[str, Any],
) -> None:
    client, _, transport = test_setup
    transport.store_document(
        "emgi.coverage-surface.v1",
        "cov-surface-blocked",
        _coverage_surface_with(
            sample_coverage_surface_payload,
            surface_id="cov-surface-blocked",
            readiness="blocked",
            cells=[
                _coverage_cell("89283082807ffff", state="empty", readiness="blocked"),
            ],
        ),
    )

    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params={"readiness": "blocked"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["surface_id"] == "cov-surface-blocked"
    assert [cell["cell_id"] for cell in data["cells"]] == ["89283082807ffff"]


def test_coverage_filter_narrows_cells_to_the_matching_subset(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
    sample_coverage_surface_payload: dict[str, Any],
) -> None:
    client, _, transport = test_setup
    transport.store_document(
        "emgi.coverage-surface.v1",
        "cov-surface-multi",
        _coverage_surface_with(
            sample_coverage_surface_payload,
            surface_id="cov-surface-multi",
            cells=[
                _coverage_cell("8928308280fffff", admin_code="63000010"),
                _coverage_cell("89283082803ffff", admin_code="63000020"),
                _coverage_cell("89283082805ffff", admin_code="63000010"),
            ],
        ),
    )

    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params={"surface_id": "cov-surface-multi", "admin_code": "63000010"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [cell["cell_id"] for cell in data["cells"]] == [
        "8928308280fffff",
        "89283082805ffff",
    ]
    assert {cell["admin_code"] for cell in data["cells"]} == {"63000010"}

    query = data["metadata"]["cell_query"]
    assert query["filters"] == {"admin_code": "63000010"}
    assert query["cell_count_published"] == 3
    assert query["cell_count_matched"] == 2
    assert query["cell_count_returned"] == 2
    assert query["truncated_by_limit"] is False


def test_coverage_limit_truncates_cells_and_reports_truncation(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
    sample_coverage_surface_payload: dict[str, Any],
) -> None:
    client, _, transport = test_setup
    transport.store_document(
        "emgi.coverage-surface.v1",
        "cov-surface-multi",
        _coverage_surface_with(
            sample_coverage_surface_payload,
            surface_id="cov-surface-multi",
            cells=[
                _coverage_cell("8928308280fffff"),
                _coverage_cell("89283082803ffff"),
                _coverage_cell("89283082805ffff"),
            ],
        ),
    )

    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params={"surface_id": "cov-surface-multi", "limit": 2},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["cells"]) == 2

    query = data["metadata"]["cell_query"]
    assert query["limit"] == 2
    assert query["cell_count_published"] == 3
    assert query["cell_count_matched"] == 3
    assert query["cell_count_returned"] == 2
    assert query["truncated_by_limit"] is True


def test_unfiltered_coverage_query_returns_the_published_surface_unannotated(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params={"surface_id": "cov-surface-001"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["cells"]) == 1
    # Nothing was narrowed, so the surface is passed through untouched.
    assert "cell_query" not in data["metadata"]
    assert data["state_breakdown"]["complete"] == 10


def test_coverage_filter_cannot_reach_another_tenants_cells(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/coverage",
        params={"admin_code": "63000010", "readiness": "ready"},
        headers=HEADERS_EXPANSION_BETA,
    )
    assert resp.status_code == 404


def test_coverage_filters_hold_when_the_transport_ignores_query_params(
    sample_coverage_surface_payload: dict[str, Any],
) -> None:
    transport = _ParamsIgnoringTransport()
    transport.store_document(
        "emgi.coverage-surface.v1", "cov-surface-001", sample_coverage_surface_payload
    )
    facade = MarketDataFacade(
        client=DataPlatformClient(transport=transport), auth_engine=AuthorizationEngine()
    )
    repo = DataPlatformMarketIntelligenceRepository(facade, transport=transport)

    with pytest.raises(MarketIntelligenceNotFoundError):
        repo.get_coverage_surface(
            "cov-surface-001",
            filters=CoverageFilter(readiness="blocked", tenant_id=TENANT_ALPHA),
            tenant_id=TENANT_ALPHA,
        )

    surface = repo.get_coverage_surface(
        "cov-surface-001",
        filters=CoverageFilter(readiness="ready", tenant_id=TENANT_ALPHA),
        tenant_id=TENANT_ALPHA,
    )
    assert [cell.cell_id for cell in surface.cells] == ["8928308280fffff"]


def test_list_data_gaps_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/data-gaps",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["gap_id"] == "gap-rent-xinyi-01"


def test_unscoped_data_gaps_are_hidden_from_every_tenant(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, transport = test_setup
    raw = transport.fetch_document("emgi.data-gap.v1", document_id="gap-doc-001")
    assert raw is not None
    raw.pop("tenant_id", None)

    for headers in (HEADERS_EXPANSION_ALPHA, HEADERS_EXPANSION_BETA):
        list_response = client.get(
            "/api/v1/market-intelligence/data-gaps",
            headers=headers,
        )
        assert list_response.status_code == 200
        assert list_response.json()["count"] == 0

        get_response = client.get(
            "/api/v1/market-intelligence/data-gaps/gap-rent-xinyi-01",
            headers=headers,
        )
        assert get_response.status_code == 404


def test_get_data_gap_by_id_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/data-gaps/gap-rent-xinyi-01",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["gap_id"] == "gap-rent-xinyi-01"
    assert data["domain"] == "rent"


# ===========================================================================
# 8. Data Acquisition Plan Endpoints
# ===========================================================================


def test_list_and_get_acquisition_plans(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/acquisition-plans",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["plan_id"] == "plan-acq-001"

    resp_single = client.get(
        "/api/v1/market-intelligence/acquisition-plans/plan-acq-001",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp_single.status_code == 200
    plan_data = resp_single.json()
    assert plan_data["plan_id"] == "plan-acq-001"
    assert plan_data["status"] == "proposed"
    assert len(plan_data["gaps"]) == 1
    assert len(plan_data["experiments"]) == 1


def test_unscoped_remote_acquisition_plans_are_hidden_from_every_tenant(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, transport = test_setup
    raw = transport.fetch_document("emgi.data-acquisition-plan.v1", document_id="plan-acq-001")
    assert raw is not None
    raw.pop("tenant_id", None)

    for headers in (HEADERS_EXPANSION_ALPHA, HEADERS_EXPANSION_BETA):
        list_response = client.get(
            "/api/v1/market-intelligence/acquisition-plans",
            headers=headers,
        )
        assert list_response.status_code == 200
        assert list_response.json()["count"] == 0

        get_response = client.get(
            "/api/v1/market-intelligence/acquisition-plans/plan-acq-001",
            headers=headers,
        )
        assert get_response.status_code == 404


def test_create_acquisition_plan_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    new_plan_id = f"plan-new-{uuid4()}"
    resp = client.post(
        "/api/v1/market-intelligence/acquisition-plans",
        json={
            "plan_id": new_plan_id,
            "site_context_id": "site-taipei-001",
            "coverage_surface_id": "cov-surface-001",
            "status": "ready",
            "plan_version": 1,
            "gaps": [
                {
                    "gap_id": "gap-poi-01",
                    "domain": "poi",
                    "measure": "poi_density",
                    "priority_rank": 1,
                    "current_uncertainty_pct": 40.0,
                    "expected_uncertainty_reduction_pct": 25.0,
                    "decision_sensitivity": 0.9,
                    "estimated_cost_units": 300.0,
                    "estimated_latency_hours": 12.0,
                    "survey_effort_hours": 3.0,
                    "quota_units": 5.0,
                    "rationale": "High density corridor survey",
                }
            ],
            "experiments": [],
            "policy": {"max_budget": 1000.0},
            "metadata": {"creator": "expansion_lead"},
        },
        headers={**HEADERS_EXPANSION_ALPHA, "Idempotency-Key": f"idem-acq-{uuid4()}"},
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["plan_id"] == new_plan_id
    assert created["status"] == "ready"


# ===========================================================================
# 9. Health & Diagnostics Endpoints
# ===========================================================================


def test_health_and_diagnostics_endpoints(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp_health = client.get("/api/v1/market-intelligence/health")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "healthy"
    assert resp_health.json()["contract"] == CONTRACT_ID

    resp_diag = client.get(
        "/api/v1/market-intelligence/diagnostics",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp_diag.status_code == 200
    diag_data = resp_diag.json()
    assert diag_data["contract"] == CONTRACT_ID
    assert diag_data["version"] == CONTRACT_VERSION


def test_create_acquisition_plan_with_canonical_experiments_success(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    """Validate that POST acquisition-plans correctly constructs canonical SourceValueExperiment models."""
    client, _, _ = test_setup
    new_plan_id = f"plan-exp-{uuid4()}"
    exp_id = f"exp-001-{uuid4()}"
    payload = {
        "plan_id": new_plan_id,
        "site_context_id": "site-taipei-001",
        "coverage_surface_id": "cov-surface-001",
        "status": "proposed",
        "plan_version": 1,
        "effective_as_of": "2026-08-14T00:00:00Z",
        "knowledge_as_of": "2026-08-14T00:00:00Z",
        "gaps": [
            {
                "gap_id": "gap-poi-01",
                "domain": "poi",
                "measure": "poi_density",
                "priority_rank": 1,
                "current_uncertainty_pct": 40.0,
                "expected_uncertainty_reduction_pct": 25.0,
                "decision_sensitivity": 0.9,
                "estimated_cost_units": 300.0,
                "estimated_latency_hours": 12.0,
                "survey_effort_hours": 3.0,
                "quota_units": 5.0,
                "rationale": "High density corridor survey",
                "recommended_source_ids": ["src-survey-01"],
            }
        ],
        "experiments": [
            {
                "experiment_id": exp_id,
                "source_id": "src-survey-01",
                "scope": "site",
                "status": "planned",
                "sample_size": 25,
                "hypothesis": "Field survey reduces foot-traffic uncertainty by 20%",
                "baseline_uncertainty_pct": 45.0,
                "expected_uplift_pct": 20.0,
                "max_cost_units": 500.0,
                "max_latency_hours": 48.0,
                "max_quota_units": 15.0,
                "survey_effort_hours": 6.0,
                "paid_source": True,
                "prior_value_evidence": False,
                "gap_ids": ["gap-poi-01"],
                "success_criteria": ["uncertainty_reduction_gte_15pct"],
            }
        ],
        "policy": {"max_budget": 2000.0, "risk_tolerance": "moderate"},
        "metadata": {"author": "expansion_planner"},
    }

    resp = client.post(
        "/api/v1/market-intelligence/acquisition-plans",
        json=payload,
        headers={**HEADERS_EXPANSION_ALPHA, "Idempotency-Key": f"idem-acq-exp-{uuid4()}"},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["plan_id"] == new_plan_id
    assert created["status"] == "proposed"
    assert len(created["experiments"]) == 1

    exp = created["experiments"][0]
    assert exp["experiment_id"] == exp_id
    assert exp["source_id"] == "src-survey-01"
    assert exp["scope"] == "site"
    assert exp["status"] == "planned"
    assert exp["sample_size"] == 25
    assert exp["hypothesis"] == "Field survey reduces foot-traffic uncertainty by 20%"
    assert exp["baseline_uncertainty_pct"] == 45.0
    assert exp["expected_uplift_pct"] == 20.0
    assert exp["max_cost_units"] == 500.0
    assert exp["paid_source"] is True
    assert exp["gap_ids"] == ["gap-poi-01"]
    assert exp["success_criteria"] == ["uncertainty_reduction_gte_15pct"]

    # Verify retrieval by plan_id
    resp_get = client.get(
        f"/api/v1/market-intelligence/acquisition-plans/{new_plan_id}",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp_get.status_code == 200
    retrieved = resp_get.json()
    assert retrieved["plan_id"] == new_plan_id
    assert len(retrieved["experiments"]) == 1
    assert retrieved["experiments"][0]["experiment_id"] == exp_id


def test_acquisition_plan_tenant_isolation(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    """Verify that locally saved acquisition plans are isolated by tenant and cross-tenant access returns 404."""
    client, _, _ = test_setup
    plan_id = f"plan-isolated-{uuid4()}"
    payload = {
        "plan_id": plan_id,
        "site_context_id": "site-taipei-001",
        "coverage_surface_id": "cov-surface-001",
        "status": "ready",
        "plan_version": 1,
        "gaps": [],
        "experiments": [],
        "policy": {"tenant": TENANT_ALPHA},
        "metadata": {"creator": "alpha_user"},
    }

    # Create plan under Tenant Alpha
    resp_create = client.post(
        "/api/v1/market-intelligence/acquisition-plans",
        json=payload,
        headers={**HEADERS_EXPANSION_ALPHA, "Idempotency-Key": f"idem-{uuid4()}"},
    )
    assert resp_create.status_code == 201

    # Tenant Alpha can get the plan
    resp_alpha = client.get(
        f"/api/v1/market-intelligence/acquisition-plans/{plan_id}",
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp_alpha.status_code == 200
    assert resp_alpha.json()["plan_id"] == plan_id

    # Tenant Beta attempting to get Tenant Alpha's plan gets 404
    resp_beta = client.get(
        f"/api/v1/market-intelligence/acquisition-plans/{plan_id}",
        headers=HEADERS_EXPANSION_BETA,
    )
    assert resp_beta.status_code == 404

    # Tenant Beta listing acquisition plans does not see Tenant Alpha's plan
    resp_list_beta = client.get(
        "/api/v1/market-intelligence/acquisition-plans",
        headers=HEADERS_EXPANSION_BETA,
    )
    assert resp_list_beta.status_code == 200
    beta_plan_ids = [p["plan_id"] for p in resp_list_beta.json().get("items", [])]
    assert plan_id not in beta_plan_ids


def test_create_acquisition_plan_invalid_enum_returns_422(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    """Verify that invalid enum values for plan status, experiment scope, and experiment status return HTTP 422."""
    client, _, _ = test_setup

    # 1. Invalid plan status
    resp_invalid_status = client.post(
        "/api/v1/market-intelligence/acquisition-plans",
        json={
            "plan_id": f"plan-err-{uuid4()}",
            "site_context_id": "site-taipei-001",
            "coverage_surface_id": "cov-surface-001",
            "status": "not_a_valid_plan_status",
            "gaps": [],
            "experiments": [],
        },
        headers={**HEADERS_EXPANSION_ALPHA, "Idempotency-Key": f"idem-{uuid4()}"},
    )
    assert resp_invalid_status.status_code == 422
    err_body = resp_invalid_status.json()
    assert (
        err_body["detail"]["code"] == "market_intelligence_validation_error" or "detail" in err_body
    )

    # 2. Invalid experiment scope
    resp_invalid_scope = client.post(
        "/api/v1/market-intelligence/acquisition-plans",
        json={
            "plan_id": f"plan-err-{uuid4()}",
            "site_context_id": "site-taipei-001",
            "coverage_surface_id": "cov-surface-001",
            "status": "proposed",
            "gaps": [],
            "experiments": [
                {
                    "experiment_id": "exp-invalid-scope",
                    "source_id": "src-01",
                    "scope": "invalid_scope_value",
                    "status": "planned",
                }
            ],
        },
        headers={**HEADERS_EXPANSION_ALPHA, "Idempotency-Key": f"idem-{uuid4()}"},
    )
    assert resp_invalid_scope.status_code == 422

    # 3. Invalid experiment status
    resp_invalid_exp_status = client.post(
        "/api/v1/market-intelligence/acquisition-plans",
        json={
            "plan_id": f"plan-err-{uuid4()}",
            "site_context_id": "site-taipei-001",
            "coverage_surface_id": "cov-surface-001",
            "status": "proposed",
            "gaps": [],
            "experiments": [
                {
                    "experiment_id": "exp-invalid-status",
                    "source_id": "src-01",
                    "scope": "site",
                    "status": "invalid_experiment_status_value",
                }
            ],
        },
        headers={**HEADERS_EXPANSION_ALPHA, "Idempotency-Key": f"idem-{uuid4()}"},
    )
    assert resp_invalid_exp_status.status_code == 422


def test_production_create_app_mounts_market_intelligence_router() -> None:
    """Ensure production create_app mounts Market Intelligence router on /api/v1 and legacy alias."""
    from apps.api.oday_api.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        resp_v1 = test_client.get("/api/v1/market-intelligence/health")
        assert resp_v1.status_code == 200
        assert resp_v1.json()["status"] == "healthy"
        assert resp_v1.json()["contract"] == CONTRACT_ID

        resp_alias = test_client.get("/market-intelligence/health")
        assert resp_alias.status_code == 200
        assert resp_alias.headers.get("Deprecation") == "true"
        assert resp_alias.json()["status"] == "healthy"


def test_create_app_wires_injected_market_data_facade() -> None:
    """App composition must pass an injected platform facade into the BFF."""
    from apps.api.oday_api.main import create_app

    facade = MarketDataFacade(
        transport=InMemoryDataPlatformTransport(),
    )
    app = create_app(market_intelligence_facade=facade)

    assert app.state.market_intelligence_facade is facade
    assert app.state.market_intelligence_service is not None
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/market-intelligence/health")
    assert response.status_code == 200
    assert response.json()["contract"] == CONTRACT_ID


def test_market_intelligence_router_requires_explicit_data_dependency() -> None:
    with pytest.raises(RuntimeError, match="requires an injected"):
        create_market_intelligence_router()


def test_list_data_gaps_with_filters(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/data-gaps",
        params={"domain": "rent", "gap_kind": "missingness", "reason_code": "LOW_SAMPLE_COUNT"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["gap_id"] == "gap-rent-xinyi-01"


def test_list_data_gaps_with_no_match_filters(
    test_setup: tuple[TestClient, MarketIntelligenceService, InMemoryDataPlatformTransport],
) -> None:
    client, _, _ = test_setup
    resp = client.get(
        "/api/v1/market-intelligence/data-gaps",
        params={"reason_code": "WRONG_REASON"},
        headers=HEADERS_EXPANSION_ALPHA,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0


def test_unavailable_router_reports_readiness_and_missingness_explicitly() -> None:
    """An unbacked BFF must declare its own missingness instead of serving empty data."""
    router = create_market_intelligence_router(
        unavailable_reason="MARKET_INTELLIGENCE_PRODUCTION_BINDING_REQUIRED",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    with TestClient(app) as client:
        health = client.get("/api/v1/market-intelligence/health")
        assert health.status_code == 200
        body = health.json()
        assert body["ready"] is False
        assert body["available"] is False
        assert body["status"] == "unavailable"
        assert body["contract"] == CONTRACT_ID
        assert body["reasonCode"] == "MARKET_INTELLIGENCE_PRODUCTION_BINDING_REQUIRED"
        assert body["missing"] == ["data_platform_binding"]

        spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        health_schema = spec["components"]["schemas"]["MarketIntelligenceHealth"]
        assert not list(Draft202012Validator(health_schema).iter_errors(body))

        for path in (
            "/api/v1/market-intelligence/cells",
            "/api/v1/market-intelligence/coverage",
            "/api/v1/market-intelligence/data-gaps",
            "/api/v1/market-intelligence/acquisition-plans",
            "/api/v1/market-intelligence/evidence/site-001",
            "/api/v1/market-intelligence/sites/site-001/context",
        ):
            response = client.get(path, headers=HEADERS_EXPANSION_ALPHA)
            assert response.status_code == 503, (path, response.text)
            assert "BINDING_REQUIRED" in response.text, path
            error_detail = response.json()["detail"]
            assert error_detail["details"]["ready"] is False
            assert error_detail["details"]["missing"] == ["data_platform_binding"]

        # Writes must fail closed too; an unbacked BFF accepts no plans.
        write = client.post(
            "/api/v1/market-intelligence/acquisition-plans",
            headers=HEADERS_EXPANSION_ALPHA,
            json={"plan_id": "plan-1", "site_context_id": "s", "coverage_surface_id": "c"},
        )
        assert write.status_code == 503, write.text


def test_production_create_app_gates_only_market_intelligence_without_binding(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """A missing platform binding must not take down production app composition."""
    from types import SimpleNamespace

    from apps.api.oday_api.main import create_app
    from shared.infrastructure.persistence.factory import _durable_bundle

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    durable = _durable_bundle(tmp_path / "market-intelligence-gate.sqlite3")
    durable.engine.is_production = True
    bundle = replace(durable, mode="postgresql", assisted_intake_store=SimpleNamespace())

    try:
        # Composition succeeds: the binding gates only the routes that consume it.
        app = create_app(persistence=bundle)
        assert app.state.market_intelligence_service is None

        with TestClient(app) as client:
            health = client.get("/api/v1/market-intelligence/health")
            assert health.status_code == 200
            assert health.json()["ready"] is False
            assert health.json()["reasonCode"] == "MARKET_INTELLIGENCE_PRODUCTION_BINDING_REQUIRED"

            cells = client.get(
                "/api/v1/market-intelligence/cells",
                headers=HEADERS_EXPANSION_ALPHA,
            )
            assert cells.status_code == 503, cells.text
            assert "MARKET_INTELLIGENCE_PRODUCTION_BINDING_REQUIRED" in cells.text
    finally:
        bundle.engine.close()
