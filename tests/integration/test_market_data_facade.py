"""Integration tests for Market Data Read Facade and Data Platform Client.

Contract: `odayplus.market-data-facade.v2`.
Task ID: `ODP-LEGACY-FACADE-001`.

Acceptance Criteria:
1. Read versioned products through generated contracts and keep product authorization in odayplus.
2. Remove provider credentials, raw fetch and source-snapshot writes from the facade path.
"""

from __future__ import annotations

import pytest
from typing import Any

from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    DataPlatformClientError,
    DataPlatformDocumentNotFoundError,
    DataPlatformIntegrityError,
    DataPlatformValidationError,
    InMemoryDataPlatformTransport,
)
from modules.external_data.application.market_data_facade import (
    ALLOWED_MARKET_DATA_ROLES,
    FACADE_CONTRACT,
    FACADE_VERSION,
    MarketDataAuthorizationError,
    MarketDataFacade,
    MarketDataFacadeError,
    MarketDataNotFoundError,
    MarketDataValidationError,
)
from packages.oday_data_contracts_client import foundation_version
from packages.oday_data_contracts_client.models import (
    EMGIPlatformFoundationConfig,
    StoreDailyPerformance,
    StoreDayCoverage,
    StoreReference,
)
from packages.oday_data_product_contracts_client import product_version
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    CatchmentProfile,
    CatchmentProfileDocument,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
    MarketCellProfileDocument,
)
from packages.oday_data_product_contracts_client.models.property_observation import (
    PropertyEntity,
    PropertyListingObservation,
    PropertyObservationDocument,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    DomainStatus,
    PeriodGrain,
    ReadinessLevel,
    SiteMarketContext,
    SiteMarketContextDocument,
)
from shared.auth import (
    ANONYMOUS,
    Action,
    DataClassification,
    Principal,
    Role,
    Scope,
)
from shared.auth.engine import AuthorizationEngine


@pytest.fixture
def sample_site_context_payload() -> dict[str, Any]:
    return {
        "document_id": "smc-doc-test-001",
        "generated_at": "2026-08-14T00:00:00Z",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "tenant_id": "tenant-alpha",
        "contexts": [
            {
                "context_id": "smc-ctx-001",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "identity": {
                    "site_id": "site-taipei-001",
                    "site_name": "Taipei Xinyi Store #1",
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
                    "brands_present": ["BrandA"],
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
                    "average_area_ping": 30.0,
                    "mean_asking_rent_per_ping": 2600.0,
                    "median_asking_rent_per_ping": 2500.0,
                },
                "mobility": {
                    "status": "available",
                    "activity_population": 15000,
                    "resident_population": 10000,
                    "work_population": 8000,
                },
                "traffic": {
                    "status": "available",
                    "mean_speed_kph": 35.0,
                    "hourly_volume_vph": 1200,
                    "motorcycle_hourly_volume": 600,
                    "motorcycle_ratio": 0.5,
                    "level_of_service": "C",
                    "is_congestion_hotspot": False,
                },
                "event": {
                    "status": "available",
                    "active_events_count": 0,
                    "events": [],
                },
                "coverage": {
                    "overall_readiness": "ready",
                    "domain_coverage": {"GEOGRAPHY": "complete"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-1"],
                    "observation_count": 100,
                    "sample_count": 100,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                },
            }
        ],
        "source_support": {
            "source_dataset_ids": ["ds-1"],
            "observation_count": 100,
            "sample_count": 100,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
        },
    }


@pytest.fixture
def sample_cell_profile_payload() -> dict[str, Any]:
    return {
        "contract_version": "emgi.market-cell-profile.v1",
        "profile_id": "mcp-doc-001",
        "product_version": "0.4.1",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "h3_resolution": 9,
        "generated_at": "2026-08-14T14:40:00Z",
        "effective_as_of": "2026-08-14T00:00:00Z",
        "knowledge_as_of": "2026-08-14T14:40:00Z",
        "tenant_id": "tenant-alpha",
        "cells": [
            {
                "cell_id": "8928308280fffff",
                "h3_index": "8928308280fffff",
                "h3_resolution": 9,
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "as_of_date": "2026-08-14",
                "county": "Taipei City",
                "district": "Xinyi",
                "demographics": {
                    "total_population": 5000.0,
                    "male_population": 2400.0,
                    "female_population": 2600.0,
                    "household_count": 1800.0,
                    "density_per_sq_km": 1000.0,
                    "daytime_population_ratio": 1.2,
                },
                "competitors": {
                    "total_competitors": 2,
                    "active_competitors": 2,
                    "competitor_density": 1.0,
                    "brands_present": ["BrandA"],
                    "stores_by_brand": {"BrandA": 2},
                    "stores_by_category": {"convenience": 2},
                },
                "rent": {
                    "mean_rent_per_ping": 2500.0,
                    "median_rent_per_ping": 2400.0,
                    "p25_rent_per_ping": 2000.0,
                    "p75_rent_per_ping": 3000.0,
                    "sample_count": 10,
                    "confidence_pct": 95.0,
                },
                "mobility": {
                    "activity_population": 3000,
                    "resident_population": 2000,
                    "work_population": 1500,
                },
                "coverage": {
                    "overall_readiness": "ready",
                    "domain_coverage": {"GEOGRAPHY": "complete"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-cell"],
                    "observation_count": 50,
                    "sample_count": 50,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                },
            }
        ],
        "source_support": {
            "source_dataset_ids": ["ds-cell"],
            "observation_count": 50,
            "sample_count": 50,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
        },
    }


@pytest.fixture
def sample_catchment_profile_payload() -> dict[str, Any]:
    return {
        "contract_version": "emgi.catchment-profile.v1",
        "document_id": "cp-doc-001",
        "product_version": "0.4.1",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "generated_at": "2026-08-14T14:40:00Z",
        "effective_as_of": "2026-08-14T00:00:00Z",
        "knowledge_as_of": "2026-08-14T14:40:00Z",
        "tenant_id": "tenant-alpha",
        "profiles": [
            {
                "profile_id": "catchment-xinyi-10m",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "origin": {
                    "origin_id": "orig-1",
                    "latitude": 25.033,
                    "longitude": 121.565,
                    "origin_h3": "8928308280fffff",
                    "origin_geom": {"type": "Point", "coordinates": [121.565, 25.033]},
                },
                "boundary": {
                    "catchment_id": "catchment-xinyi-10m",
                    "travel_mode": "pedestrian",
                    "cutoff_seconds": 600,
                    "routing_engine": "valhalla",
                    "graph_version": "v1.0",
                    "area_sq_meters": 50000.0,
                    "estimation_status": "exact",
                    "h3_cells": ["8928308280fffff"],
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
                "demographics": {"status": "available"},
                "competitors": {"status": "available"},
                "rent": {"status": "available"},
                "mobility": {"status": "available"},
                "traffic": {"status": "available"},
                "coverage": {
                    "status": "available",
                    "overall_readiness": "ready",
                    "domain_coverage": {"MOBILITY": "complete"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-cat"],
                    "observation_count": 80,
                    "sample_count": 80,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                },
            }
        ],
        "source_support": {
            "source_dataset_ids": ["ds-cat"],
            "observation_count": 80,
            "sample_count": 80,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
        },
    }


@pytest.fixture
def sample_property_observation_payload() -> dict[str, Any]:
    return {
        "created_at": "2026-08-14T00:00:00Z",
        "contract_id": "emgi.property-observation.v1",
        "properties": [
            {
                "property_id": "prop-tw-001",
                "county": "Taipei City",
                "district": "Xinyi",
                "normalized_address": "No. 1, Sec. 5, Xinyi Rd.",
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-14T00:00:00Z",
            }
        ],
        "listing_observations": [
            {
                "listing_obs_id": "list-obs-001",
                "property_id": "prop-tw-001",
                "channel": "listing_portal",
                "observed_at": "2026-08-14T00:00:00Z",
                "first_seen_at": "2026-08-01T00:00:00Z",
                "last_seen_at": "2026-08-14T00:00:00Z",
                "listing_status": "ACTIVE",
                "listing_kind": "RENTAL",
                "monthly_rent": 65000,
                "floor_area_ping": 28.5,
            }
        ],
        "status_histories": [],
    }


@pytest.fixture
def sample_store_reference_payload() -> dict[str, Any]:
    return {
        "store_id": "store-101",
        "store_name": "Taipei Xinyi Store",
        "effective_from": "2026-01-01T00:00:00+08:00",
        "registered_at": "2025-12-01T00:00:00+08:00",
        "source_row_digest": "a" * 64,
        "time_contract": {"knowledge_as_of": "2026-01-02T00:00:00+08:00"},
        "geolocation": {"latitude": 25.033, "longitude": 121.565},
    }


@pytest.fixture
def sample_store_coverage_payload() -> dict[str, Any]:
    return {
        "store_id": "store-101",
        "business_date": "2026-08-14",
        "window_start": "2026-08-14T00:00:00+08:00",
        "window_end": "2026-08-14T23:59:59+08:00",
        "raw_contract_fingerprint": "b" * 64,
        "coverage": {
            "coverage_id": "cov-101",
            "dataset_id": "ds-orders",
            "scope_principal_id": "sp-1",
            "state": "complete",
            "is_complete": True,
        },
    }


@pytest.fixture
def sample_store_performance_payload() -> dict[str, Any]:
    return {
        "store_id": "store-101",
        "business_date": "2026-08-14",
        "window_start": "2026-08-14T00:00:00+08:00",
        "window_end": "2026-08-14T23:59:59+08:00",
        "coverage_id": "cov-101",
        "coverage_state": "complete",
        "is_complete": True,
        "raw_contract_fingerprint": "c" * 64,
        "time_contract": {"knowledge_as_of": "2026-08-14T23:59:59+08:00"},
        "transaction_count": 350,
        "gross_amount": 52500.0,
        "paid_amount": 50000.0,
    }


@pytest.fixture
def sample_foundation_config_payload() -> dict[str, Any]:
    return {
        "contract_id": "emgi.platform-foundation.v1",
        "contract_version": "1.0.0",
        "default_crs": 4326,
        "taiwan_crs": 3826,
        "object_store_backend": "gcs",
    }


@pytest.fixture
def seeded_transport(
    sample_site_context_payload,
    sample_cell_profile_payload,
    sample_catchment_profile_payload,
    sample_property_observation_payload,
    sample_store_reference_payload,
    sample_store_coverage_payload,
    sample_store_performance_payload,
    sample_foundation_config_payload,
) -> InMemoryDataPlatformTransport:
    transport = InMemoryDataPlatformTransport()
    # Store documents
    transport.store_document("emgi.site-market-context.v1", "smc-doc-test-001", sample_site_context_payload)
    transport.store_document("emgi.market-cell-profile.v1", "mcp-doc-001", sample_cell_profile_payload)
    transport.store_document("emgi.catchment-profile.v1", "cp-doc-001", sample_catchment_profile_payload)
    transport.store_document("emgi.property-observation.v1", "prop-doc-001", sample_property_observation_payload)
    transport.store_document("emgi.platform-foundation.v1", "foundation-cfg", sample_foundation_config_payload)
    transport.store_document("oday.store-reference.v1", "store-101", sample_store_reference_payload)
    transport.store_document("oday.store-coverage.v1", "store-101:2026-08-14", sample_store_coverage_payload)
    transport.store_document("oday.store-daily-performance.v1", "store-101:2026-08-14", sample_store_performance_payload)

    # Index specific entities
    transport.store_site_context("site-taipei-001", sample_site_context_payload["contexts"][0])
    transport.store_cell_profile("8928308280fffff", sample_cell_profile_payload["cells"][0])
    transport.store_catchment_profile("catchment-xinyi-10m", sample_catchment_profile_payload["profiles"][0])
    transport.store_property_entity("prop-tw-001", sample_property_observation_payload["properties"][0])
    transport.store_listing_observation("list-obs-001", sample_property_observation_payload["listing_observations"][0])
    transport.store_store_reference("store-101", sample_store_reference_payload)
    transport.store_store_coverage_record("store-101", "2026-08-14", sample_store_coverage_payload)
    transport.store_store_daily_performance_record("store-101", "2026-08-14", sample_store_performance_payload)
    return transport


@pytest.fixture
def client(seeded_transport) -> DataPlatformClient:
    return DataPlatformClient(transport=seeded_transport)


@pytest.fixture
def facade(client) -> MarketDataFacade:
    return MarketDataFacade(client=client, enforce_auth=True)


@pytest.fixture
def expansion_principal() -> Principal:
    return Principal(
        subject_id="user-expansion-1",
        roles=frozenset({Role.EXPANSION_USER}),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )


@pytest.fixture
def site_reviewer_principal() -> Principal:
    return Principal(
        subject_id="user-reviewer-1",
        roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )


@pytest.fixture
def data_owner_principal() -> Principal:
    return Principal(
        subject_id="user-dataowner-1",
        roles=frozenset({Role.DATA_OWNER}),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.HIGHLY_RESTRICTED),
        authenticated=True,
    )


@pytest.fixture
def platform_admin_principal() -> Principal:
    return Principal(
        subject_id="admin-platform-1",
        roles=frozenset({Role.PLATFORM_ADMIN}),
        scope=Scope(clearance=DataClassification.HIGHLY_RESTRICTED),
        authenticated=True,
    )


@pytest.fixture
def foreign_tenant_principal() -> Principal:
    return Principal(
        subject_id="user-tenant-beta",
        roles=frozenset({Role.EXPANSION_USER}),
        scope=Scope(tenant_id="tenant-beta", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )


# ===========================================================================
# 1. Contract Identity & Release Verification Tests
# ===========================================================================

def test_contract_identity_and_version(facade):
    assert FACADE_CONTRACT == "odayplus.market-data-facade.v2"
    assert FACADE_VERSION == "2.0.0"
    assert facade.contract == FACADE_CONTRACT
    assert facade.version == FACADE_VERSION


def test_data_platform_client_versions_and_integrity(client):
    found_ver = client.get_foundation_version()
    prod_ver = client.get_product_version()

    assert found_ver.client_contract == "odayplus.data-platform-foundation-client.v1"
    assert prod_ver.client_contract == "odayplus.data-platform-product-client.v1"
    assert found_ver.status == "PUBLISHED"
    assert prod_ver.status == "PUBLISHED"

    integrity = client.verify_integrity()
    assert integrity["status"] == "healthy"
    assert integrity["foundation"]["compatible"] is True
    assert integrity["product"]["compatible"] is True


def test_facade_diagnostics_and_health_check(facade):
    diag = facade.get_diagnostics()
    assert diag["facade_contract"] == FACADE_CONTRACT
    assert diag["facade_version"] == FACADE_VERSION
    assert "foundation" in diag["client_diagnostics"]
    assert "product" in diag["client_diagnostics"]

    health = facade.check_health()
    assert health["status"] == "healthy"
    assert health["contract"] == FACADE_CONTRACT
    assert health["version"] == FACADE_VERSION


# ===========================================================================
# 2. DataPlatformClient Direct Reads & Model Parsing
# ===========================================================================

def test_client_site_market_context_reads(client):
    doc = client.get_site_market_context_document(document_id="smc-doc-test-001")
    assert isinstance(doc, SiteMarketContextDocument)
    assert doc.document_id == "smc-doc-test-001"
    assert doc.period_grain is PeriodGrain.MONTHLY
    assert len(doc.contexts) == 1

    ctx = client.get_site_market_context("site-taipei-001")
    assert isinstance(ctx, SiteMarketContext)
    assert ctx.identity.site_id == "site-taipei-001"
    assert ctx.identity.latitude == 25.033
    assert ctx.catchment.status is DomainStatus.available
    assert ctx.demand.total_population == 50000.0
    assert ctx.poi.total_poi_count == 500
    assert ctx.rent.mean_rent_per_ping == 2500.0
    assert ctx.coverage.overall_readiness is ReadinessLevel.ready


def test_client_market_cell_profile_reads(client):
    doc = client.get_market_cell_profile_document(document_id="mcp-doc-001")
    assert isinstance(doc, MarketCellProfileDocument)
    assert doc.profile_id == "mcp-doc-001"
    assert len(doc.cells) == 1

    prof = client.get_market_cell_profile("8928308280fffff")
    assert isinstance(prof, MarketCellProfile)
    assert prof.cell_id == "8928308280fffff"
    assert prof.demographics.total_population == 5000.0
    assert prof.competitors.total_competitors == 2


def test_client_catchment_profile_reads(client):
    doc = client.get_catchment_profile_document(document_id="cp-doc-001")
    assert isinstance(doc, CatchmentProfileDocument)
    assert doc.document_id == "cp-doc-001"
    assert len(doc.profiles) == 1

    prof = client.get_catchment_profile("catchment-xinyi-10m")
    assert isinstance(prof, CatchmentProfile)
    assert prof.profile_id == "catchment-xinyi-10m"
    assert prof.boundary.cutoff_seconds == 600
    assert prof.boundary.catchment_id == "catchment-xinyi-10m"


def test_client_property_and_listing_reads(client):
    doc = client.get_property_observation_document(document_id="prop-doc-001")
    assert isinstance(doc, PropertyObservationDocument)
    assert len(doc.properties) == 1
    assert len(doc.listing_observations) == 1

    prop = client.get_property_entity("prop-tw-001")
    assert isinstance(prop, PropertyEntity)
    assert prop.property_id == "prop-tw-001"
    assert prop.district == "Xinyi"

    listing = client.get_listing_observation("list-obs-001")
    assert isinstance(listing, PropertyListingObservation)
    assert listing.listing_obs_id == "list-obs-001"
    assert listing.monthly_rent == 65000


def test_client_foundation_reads(client):
    cfg = client.get_platform_foundation_config()
    assert isinstance(cfg, EMGIPlatformFoundationConfig)
    assert cfg.default_crs == 4326

    store = client.get_store_reference("store-101")
    assert isinstance(store, StoreReference)
    assert store.store_id == "store-101"
    assert store.store_name == "Taipei Xinyi Store"

    cov = client.get_store_day_coverage("store-101", "2026-08-14")
    assert isinstance(cov, StoreDayCoverage)
    assert cov.coverage.is_complete is True

    perf = client.get_store_daily_performance("store-101", "2026-08-14")
    assert isinstance(perf, StoreDailyPerformance)
    assert perf.transaction_count == 350


# ===========================================================================
# 3. DataPlatformClient Error Handling
# ===========================================================================

def test_client_document_not_found_errors(client):
    with pytest.raises(DataPlatformDocumentNotFoundError) as exc_info:
        client.get_site_market_context("non-existent-site")
    assert exc_info.value.code == "document_not_found"

    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_market_cell_profile("non-existent-cell")

    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_catchment_profile("non-existent-catchment")

    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_property_entity("non-existent-prop")

    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_listing_observation("non-existent-list")


def test_client_validation_error_on_corrupt_payload(seeded_transport):
    # Store corrupt document
    seeded_transport.store_document(
        "emgi.site-market-context.v1",
        "corrupt-doc",
        {"document_id": "corrupt-doc", "period_grain": "INVALID_GRAIN"},
    )
    client = DataPlatformClient(transport=seeded_transport)
    with pytest.raises(DataPlatformValidationError) as exc_info:
        client.get_site_market_context_document(document_id="corrupt-doc")
    assert exc_info.value.code == "contract_validation_error"


# ===========================================================================
# 4. MarketDataFacade Authorized Queries
# ===========================================================================

def test_facade_authorized_site_market_context(facade, expansion_principal):
    ctx = facade.get_site_market_context("site-taipei-001", tenant_id="tenant-alpha", principal=expansion_principal)
    assert isinstance(ctx, SiteMarketContext)
    assert ctx.identity.site_id == "site-taipei-001"
    assert ctx.coverage.overall_readiness is ReadinessLevel.ready


def test_facade_authorized_market_cell_profile(facade, site_reviewer_principal):
    prof = facade.get_market_cell_profile("8928308280fffff", tenant_id="tenant-alpha", principal=site_reviewer_principal)
    assert isinstance(prof, MarketCellProfile)
    assert prof.cell_id == "8928308280fffff"


def test_facade_authorized_catchment_profile(facade, expansion_principal):
    prof = facade.get_catchment_profile("catchment-xinyi-10m", tenant_id="tenant-alpha", principal=expansion_principal)
    assert isinstance(prof, CatchmentProfile)
    assert prof.profile_id == "catchment-xinyi-10m"


def test_facade_authorized_property_and_listing(facade, site_reviewer_principal):
    prop = facade.get_property_entity("prop-tw-001", tenant_id="tenant-alpha", principal=site_reviewer_principal)
    assert isinstance(prop, PropertyEntity)
    assert prop.property_id == "prop-tw-001"

    listing = facade.get_listing_observation("list-obs-001", tenant_id="tenant-alpha", principal=site_reviewer_principal)
    assert isinstance(listing, PropertyListingObservation)
    assert listing.listing_obs_id == "list-obs-001"


def test_facade_authorized_foundation_datasets(facade, data_owner_principal):
    store = facade.get_store_reference("store-101", principal=data_owner_principal)
    assert isinstance(store, StoreReference)
    assert store.store_id == "store-101"

    cov = facade.get_store_day_coverage("store-101", "2026-08-14", principal=data_owner_principal)
    assert isinstance(cov, StoreDayCoverage)
    assert cov.coverage.is_complete is True

    perf = facade.get_store_daily_performance("store-101", "2026-08-14", principal=data_owner_principal)
    assert isinstance(perf, StoreDailyPerformance)
    assert perf.transaction_count == 350

    cfg = facade.get_platform_foundation_config(principal=data_owner_principal)
    assert isinstance(cfg, EMGIPlatformFoundationConfig)


# ===========================================================================
# 5. MarketDataFacade Authorization & Security Gates
# ===========================================================================

def test_facade_requires_principal_when_auth_enforced(facade):
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context("site-taipei-001", principal=None)
    assert exc_info.value.code == "authentication_required"


def test_facade_unauthenticated_principal_denied(facade):
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context("site-taipei-001", principal=ANONYMOUS)
    assert exc_info.value.code == "unauthenticated_principal"


def test_facade_unauthorized_role_rejected(facade):
    custom_role_principal = Principal(
        subject_id="unknown-role-user",
        roles=frozenset({"unknown_role"}),  # type: ignore
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context("site-taipei-001", tenant_id="tenant-alpha", principal=custom_role_principal)
    assert exc_info.value.code == "role_unauthorized"


def test_facade_cross_tenant_isolation_denied(facade, foreign_tenant_principal):
    # Principal from tenant-beta attempts to read resource for tenant-alpha
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-taipei-001",
            tenant_id="tenant-alpha",
            principal=foreign_tenant_principal,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"


def test_facade_platform_admin_can_bypass_tenant_isolation(facade, platform_admin_principal):
    ctx = facade.get_site_market_context(
        "site-taipei-001",
        tenant_id="tenant-alpha",
        principal=platform_admin_principal,
    )
    assert isinstance(ctx, SiteMarketContext)
    assert ctx.identity.site_id == "site-taipei-001"


def test_facade_insufficient_clearance_denied(facade):
    low_clearance_principal = Principal(
        subject_id="low-clearance-user",
        roles=frozenset({Role.EXPANSION_USER}),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.PUBLIC),
        authenticated=True,
    )
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-taipei-001",
            tenant_id="tenant-alpha",
            principal=low_clearance_principal,
        )
    assert exc_info.value.code == "insufficient_clearance"


# ===========================================================================
# 6. MarketDataFacade Error Propagation
# ===========================================================================

def test_facade_not_found_raises_market_data_not_found(facade, expansion_principal):
    with pytest.raises(MarketDataNotFoundError) as exc_info:
        facade.get_site_market_context("missing-site", tenant_id="tenant-alpha", principal=expansion_principal)
    assert exc_info.value.code == "market_data_not_found"


def test_facade_validation_error_raises_market_data_validation(seeded_transport, expansion_principal):
    # Insert corrupt site context
    seeded_transport.store_document(
        "emgi.site-market-context.v1",
        "bad-doc",
        {"document_id": "bad-doc", "period_grain": "BAD_VAL"},
    )
    client = DataPlatformClient(transport=seeded_transport)
    facade = MarketDataFacade(client=client, enforce_auth=True)

    with pytest.raises(MarketDataValidationError) as exc_info:
        facade.get_site_market_context_document(document_id="bad-doc", tenant_id="tenant-alpha", principal=expansion_principal)
    assert exc_info.value.code == "market_data_validation_error"


# ===========================================================================
# 7. Architectural Invariants & Boundary Compliance
# ===========================================================================

def test_facade_strictly_read_only_no_write_or_provider_credentials():
    # Assert facade and client have zero write / mutation / credential methods
    banned_prefixes = ("save_", "insert_", "update_", "delete_", "crawl_", "fetch_raw_", "write_")
    for cls in (MarketDataFacade, DataPlatformClient):
        for attr in dir(cls):
            assert not any(attr.startswith(p) for p in banned_prefixes), f"Banned method {attr} found on {cls.__name__}"
