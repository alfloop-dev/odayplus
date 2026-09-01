"""Integration tests for Market Data Read Facade and Data Platform Client.

Contract: `odayplus.market-data-facade.v2`.
Task ID: `ODP-LEGACY-FACADE-001`.

Acceptance Criteria:
1. Read versioned products through generated contracts and keep product authorization in odayplus.
2. Remove provider credentials, raw fetch and source-snapshot writes from the facade path.
"""

from __future__ import annotations

from typing import Any

import pytest

from modules.external_data.application.market_data_facade import (
    FACADE_CONTRACT,
    FACADE_VERSION,
    MarketDataAuthorizationError,
    MarketDataFacade,
    MarketDataFacadeError,
    MarketDataNotFoundError,
    MarketDataValidationError,
)
from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    DataPlatformClientError,
    DataPlatformDocumentNotFoundError,
    InMemoryDataPlatformTransport,
)
from packages.oday_data_contracts_client.models import (
    EMGIPlatformFoundationConfig,
    OperationalStartObservation,
    StoreDailyPerformance,
    StoreDayCoverage,
    StoreReference,
)
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
def sample_operational_start_payload() -> dict[str, Any]:
    return {
        "contract_id": "oday.operational-start-observation.v1",
        "contract_version": "1.0.0",
        "store_id": "store-101",
        "method": "FIRST_OBSERVED_TRANSACTION",
        "confidence": "HIGH",
        "observed_start_business_date": "2026-01-01",
        "observation_window_start": "2026-01-01T00:00:00+08:00",
        "observation_window_end": "2026-08-14T23:59:59+08:00",
        "is_left_censored": False,
        "is_operator_truth": False,
        "time_contract": {"knowledge_as_of": "2026-08-14T23:59:59+08:00"},
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
    sample_operational_start_payload,
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
    transport.store_document("oday.operational-start-observation.v1", "store-101", sample_operational_start_payload)
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


# ===========================================================================
# 2. DataPlatformClient Direct Reads & Model Parsing
# ===========================================================================

def test_client_site_market_context_reads(client):
    doc = client.get_site_market_context_document(document_id="smc-doc-test-001")
    assert isinstance(doc, SiteMarketContextDocument)
    assert doc.document_id == "smc-doc-test-001"
    assert doc.period_grain is PeriodGrain.MONTHLY
    assert len(doc.contexts) == 1

    ctx = client.get_site_market_context("site-taipei-001", period_grain=PeriodGrain.MONTHLY, period_key="2026-08")
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

    prof = client.get_market_cell_profile("8928308280fffff", period_grain=PeriodGrain.MONTHLY, period_key="2026-08")
    assert isinstance(prof, MarketCellProfile)
    assert prof.cell_id == "8928308280fffff"
    assert prof.demographics.total_population == 5000.0
    assert prof.competitors.total_competitors == 2


def test_client_catchment_profile_reads(client):
    doc = client.get_catchment_profile_document(document_id="cp-doc-001")
    assert isinstance(doc, CatchmentProfileDocument)
    assert doc.document_id == "cp-doc-001"
    assert len(doc.profiles) == 1

    prof = client.get_catchment_profile("catchment-xinyi-10m", period_grain=PeriodGrain.MONTHLY, period_key="2026-08")
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

    op_start = client.get_operational_start_observation("store-101")
    assert isinstance(op_start, OperationalStartObservation)
    assert op_start.store_id == "store-101"
    assert op_start.observed_start_business_date == "2026-01-01"


# ===========================================================================
# 3. Period Grain & Period Key Filtering (Issue C1)
# ===========================================================================

def test_client_period_grain_and_key_mismatch_raises_not_found(client):
    # Querying DAILY grain when only MONTHLY exists must raise DataPlatformDocumentNotFoundError
    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_site_market_context(
            "site-taipei-001",
            period_grain=PeriodGrain.DAILY,
            period_key="1999-01",
        )

    # Querying mismatching period_key must raise DataPlatformDocumentNotFoundError
    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_site_market_context(
            "site-taipei-001",
            period_grain=PeriodGrain.MONTHLY,
            period_key="1999-01",
        )

    # Market cell mismatch
    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_market_cell_profile(
            "8928308280fffff",
            period_grain=PeriodGrain.DAILY,
            period_key="1999-01",
        )

    # Catchment mismatch
    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_catchment_profile(
            "catchment-xinyi-10m",
            period_grain=PeriodGrain.DAILY,
            period_key="1999-01",
        )


def test_facade_period_grain_and_key_mismatch_raises_not_found(facade, expansion_principal):
    with pytest.raises(MarketDataNotFoundError):
        facade.get_site_market_context(
            "site-taipei-001",
            period_grain=PeriodGrain.DAILY,
            period_key="1999-01",
            tenant_id="tenant-alpha",
            principal=expansion_principal,
        )

    with pytest.raises(MarketDataNotFoundError):
        facade.get_market_cell_profile(
            "8928308280fffff",
            period_grain=PeriodGrain.DAILY,
            period_key="1999-01",
            tenant_id="tenant-alpha",
            principal=expansion_principal,
        )

    with pytest.raises(MarketDataNotFoundError):
        facade.get_catchment_profile(
            "catchment-xinyi-10m",
            period_grain=PeriodGrain.DAILY,
            period_key="1999-01",
            tenant_id="tenant-alpha",
            principal=expansion_principal,
        )


# ===========================================================================
# 4. Custom Transport Protocol Compatibility (Issue C2)
# ===========================================================================

def test_client_works_with_custom_transport_protocol(sample_site_context_payload):
    class CustomMockTransport:
        def fetch_document(
            self,
            contract_id: str,
            *,
            document_id: str | None = None,
            params: Any = None,
        ) -> dict[str, Any] | None:
            if contract_id == "emgi.site-market-context.v1":
                return sample_site_context_payload
            return None

        def query_records(self, contract_id: str, *, filter_params: Any = None) -> list[dict[str, Any]]:
            return []

    custom_client = DataPlatformClient(transport=CustomMockTransport())
    ctx = custom_client.get_site_market_context("site-taipei-001", period_grain=PeriodGrain.MONTHLY, period_key="2026-08")
    assert isinstance(ctx, SiteMarketContext)
    assert ctx.identity.site_id == "site-taipei-001"


# ===========================================================================
# 5. MarketDataFacade Authorized Queries
# ===========================================================================

def test_facade_authorized_site_market_context(facade, expansion_principal):
    ctx = facade.get_site_market_context(
        "site-taipei-001",
        period_grain=PeriodGrain.MONTHLY,
        period_key="2026-08",
        tenant_id="tenant-alpha",
        principal=expansion_principal,
    )
    assert isinstance(ctx, SiteMarketContext)
    assert ctx.identity.site_id == "site-taipei-001"
    assert ctx.coverage.overall_readiness is ReadinessLevel.ready


def test_facade_authorized_market_cell_profile(facade, site_reviewer_principal):
    prof = facade.get_market_cell_profile(
        "8928308280fffff",
        period_grain=PeriodGrain.MONTHLY,
        period_key="2026-08",
        tenant_id="tenant-alpha",
        principal=site_reviewer_principal,
    )
    assert isinstance(prof, MarketCellProfile)
    assert prof.cell_id == "8928308280fffff"


def test_facade_authorized_catchment_profile(facade, expansion_principal):
    prof = facade.get_catchment_profile(
        "catchment-xinyi-10m",
        period_grain=PeriodGrain.MONTHLY,
        period_key="2026-08",
        tenant_id="tenant-alpha",
        principal=expansion_principal,
    )
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

    op_start = facade.get_operational_start_observation("store-101", principal=data_owner_principal)
    assert isinstance(op_start, OperationalStartObservation)
    assert op_start.store_id == "store-101"

    cfg = facade.get_platform_foundation_config(principal=data_owner_principal)
    assert isinstance(cfg, EMGIPlatformFoundationConfig)


# ===========================================================================
# 6. MarketDataFacade Authorization & Security Gates (Issue B1)
# ===========================================================================

def test_facade_requires_principal_when_auth_enforced(facade):
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context("site-taipei-001", principal=None)
    assert exc_info.value.code == "authentication_required"


def test_facade_unauthenticated_principal_denied(facade):
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context("site-taipei-001", principal=ANONYMOUS)
    assert exc_info.value.code == "unauthenticated_principal"


def test_facade_principal_with_zero_roles_denied(facade):
    """Authenticated principal with empty roles must be denied with role_unauthorized."""
    zero_role_principal = Principal(
        subject_id="user-no-roles",
        roles=frozenset(),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context("site-taipei-001", tenant_id="tenant-alpha", principal=zero_role_principal)
    assert exc_info.value.code == "role_unauthorized"


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
# 7. MarketDataFacade Error Propagation
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
# 8. Architectural Invariants & Boundary Compliance
# ===========================================================================

def test_facade_strictly_read_only_no_write_or_provider_credentials():
    # Assert facade and client have zero write / mutation / credential methods
    banned_prefixes = ("save_", "insert_", "update_", "delete_", "crawl_", "fetch_raw_", "write_")
    for cls in (MarketDataFacade, DataPlatformClient):
        for attr in dir(cls):
            assert not any(attr.startswith(p) for p in banned_prefixes), f"Banned method {attr} found on {cls.__name__}"


# ===========================================================================
# 9. Rejection Findings Regression Tests (B1, C1, C2)
# ===========================================================================

def test_b1_rbac_bypass_authenticated_principal_zero_roles_rejected(facade):
    """B1: Authenticated principal with zero roles must not bypass RBAC."""
    zero_role_principal = Principal(
        subject_id="zero-role-attacker",
        roles=frozenset(),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context("site-taipei-001", tenant_id="tenant-alpha", principal=zero_role_principal)
    assert exc_info.value.code == "role_unauthorized"


def test_c1_period_grain_and_period_key_filtering(client, facade, expansion_principal):
    """C1: period_grain and period_key must not be bypassed or ignored."""
    # Seeded document has period_grain=MONTHLY, period_key=2026-08
    # Exact match works
    ctx = client.get_site_market_context("site-taipei-001", period_grain=PeriodGrain.MONTHLY, period_key="2026-08")
    assert ctx.period_grain is PeriodGrain.MONTHLY
    assert ctx.period_key == "2026-08"

    # Mismatched grain (DAILY instead of MONTHLY) raises DocumentNotFound
    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_site_market_context("site-taipei-001", period_grain=PeriodGrain.DAILY, period_key="2026-08")

    # Mismatched period_key raises DocumentNotFound
    with pytest.raises(DataPlatformDocumentNotFoundError):
        client.get_site_market_context("site-taipei-001", period_grain=PeriodGrain.MONTHLY, period_key="1999-01")

    # Facade wraps into MarketDataNotFoundError
    with pytest.raises(MarketDataNotFoundError):
        facade.get_site_market_context(
            "site-taipei-001",
            period_grain=PeriodGrain.DAILY,
            period_key="1999-01",
            tenant_id="tenant-alpha",
            principal=expansion_principal,
        )


def test_c2_polymorphic_transport_compatibility(sample_site_context_payload):
    """C2: DataPlatformClient works with custom transport protocol without isinstance branching."""
    class CustomTransport:
        def fetch_document(
            self,
            contract_id: str,
            *,
            document_id: str | None = None,
            params: Any = None,
        ) -> dict[str, Any] | None:
            if contract_id == "emgi.site-market-context.v1":
                return sample_site_context_payload
            return None

        def query_records(self, contract_id: str, *, filter_params: Any = None) -> list[dict[str, Any]]:
            return []

    client_custom = DataPlatformClient(transport=CustomTransport())
    ctx = client_custom.get_site_market_context("site-taipei-001", period_grain=PeriodGrain.MONTHLY, period_key="2026-08")
    assert ctx.identity.site_id == "site-taipei-001"


# ===========================================================================
# 10. Rejection Round 2 Regression Tests (T1, T2, T3, M1, M2)
# ===========================================================================

def test_t1_t2_two_tenant_isolation_and_default_scoping(
    sample_site_context_payload,
    sample_cell_profile_payload,
    sample_catchment_profile_payload,
    sample_property_observation_payload,
):
    """T1 & T2: Multi-tenant test verifying default tenant scoping and full property entity isolation."""
    # Build distinct payloads for tenant-beta
    site_beta_payload = dict(sample_site_context_payload)
    site_beta_payload["document_id"] = "smc-doc-beta-001"
    site_beta_payload["tenant_id"] = "tenant-beta"
    site_beta_payload["contexts"] = [
        {
            **sample_site_context_payload["contexts"][0],
            "context_id": "smc-ctx-beta-001",
            "identity": {
                **sample_site_context_payload["contexts"][0]["identity"],
                "site_id": "site-beta-001",
                "site_name": "Beta Store #1",
            },
        }
    ]

    cell_beta_payload = dict(sample_cell_profile_payload)
    cell_beta_payload["profile_id"] = "mcp-doc-beta-001"
    cell_beta_payload["tenant_id"] = "tenant-beta"
    cell_beta_payload["cells"] = [
        {
            **sample_cell_profile_payload["cells"][0],
            "cell_id": "8928308280bbbbb",
            "h3_index": "8928308280bbbbb",
        }
    ]

    cat_beta_payload = dict(sample_catchment_profile_payload)
    cat_beta_payload["document_id"] = "cp-doc-beta-001"
    cat_beta_payload["tenant_id"] = "tenant-beta"
    cat_beta_payload["profiles"] = [
        {
            **sample_catchment_profile_payload["profiles"][0],
            "profile_id": "catchment-beta-10m",
            "boundary": {
                **sample_catchment_profile_payload["profiles"][0]["boundary"],
                "catchment_id": "catchment-beta-10m",
            },
        }
    ]

    prop_beta_payload = {
        "created_at": "2026-08-14T00:00:00Z",
        "contract_id": "emgi.property-observation.v1",
        "tenant_id": "tenant-beta",
        "properties": [
            {
                "property_id": "prop-beta-001",
                "county": "Taichung City",
                "district": "Xitun",
                "normalized_address": "No. 100, Taiwan Blvd.",
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-14T00:00:00Z",
            }
        ],
        "listing_observations": [
            {
                "listing_obs_id": "list-beta-001",
                "property_id": "prop-beta-001",
                "channel": "listing_portal",
                "observed_at": "2026-08-14T00:00:00Z",
                "first_seen_at": "2026-08-01T00:00:00Z",
                "last_seen_at": "2026-08-14T00:00:00Z",
                "listing_status": "ACTIVE",
                "listing_kind": "RENTAL",
                "monthly_rent": 45000,
                "floor_area_ping": 22.0,
            }
        ],
        "status_histories": [],
    }

    prop_alpha_payload = {
        **sample_property_observation_payload,
        "tenant_id": "tenant-alpha",
    }

    transport = InMemoryDataPlatformTransport()
    # Seed tenant-alpha
    transport.store_document("emgi.site-market-context.v1", "smc-doc-test-001", sample_site_context_payload)
    transport.store_document("emgi.market-cell-profile.v1", "mcp-doc-001", sample_cell_profile_payload)
    transport.store_document("emgi.catchment-profile.v1", "cp-doc-001", sample_catchment_profile_payload)
    transport.store_document("emgi.property-observation.v1", "prop-doc-001", prop_alpha_payload)

    # Seed tenant-beta
    transport.store_document("emgi.site-market-context.v1", "smc-doc-beta-001", site_beta_payload)
    transport.store_document("emgi.market-cell-profile.v1", "mcp-doc-beta-001", cell_beta_payload)
    transport.store_document("emgi.catchment-profile.v1", "cp-doc-beta-001", cat_beta_payload)
    transport.store_document("emgi.property-observation.v1", "prop-doc-beta-001", prop_beta_payload)

    client = DataPlatformClient(transport=transport)
    facade = MarketDataFacade(client=client, enforce_auth=True)

    principal_alpha = Principal(
        subject_id="user-alpha",
        roles=frozenset({Role.EXPANSION_USER}),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )
    principal_beta = Principal(
        subject_id="user-beta",
        roles=frozenset({Role.EXPANSION_USER}),
        scope=Scope(tenant_id="tenant-beta", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )

    # T2: Omitting tenant_id defaults to principal.tenant_id
    ctx_alpha = facade.get_site_market_context(
        "site-taipei-001",
        period_grain=PeriodGrain.MONTHLY,
        period_key="2026-08",
        principal=principal_alpha,
    )
    assert ctx_alpha.identity.site_id == "site-taipei-001"

    ctx_beta = facade.get_site_market_context(
        "site-beta-001",
        period_grain=PeriodGrain.MONTHLY,
        period_key="2026-08",
        principal=principal_beta,
    )
    assert ctx_beta.identity.site_id == "site-beta-001"

    # T2: Cross-tenant read attempts with explicit foreign tenant are denied by gate
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-beta-001",
            period_grain=PeriodGrain.MONTHLY,
            period_key="2026-08",
            tenant_id="tenant-beta",
            principal=principal_alpha,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # T2: Attempting to read foreign site without tenant_id raises NotFound in own tenant (no cross-tenant leak)
    with pytest.raises(MarketDataNotFoundError):
        facade.get_site_market_context(
            "site-beta-001",
            period_grain=PeriodGrain.MONTHLY,
            period_key="2026-08",
            principal=principal_alpha,
        )

    # T1: Property Entity and Listing Observation isolation
    prop_a = facade.get_property_entity("prop-tw-001", principal=principal_alpha)
    assert prop_a.property_id == "prop-tw-001"

    prop_b = facade.get_property_entity("prop-beta-001", principal=principal_beta)
    assert prop_b.property_id == "prop-beta-001"

    # Principal Alpha querying Principal Beta's property entity raises NotFound (does not leak)
    with pytest.raises(MarketDataNotFoundError):
        facade.get_property_entity("prop-beta-001", principal=principal_alpha)

    # Principal Beta querying Principal Alpha's property entity raises NotFound (does not leak)
    with pytest.raises(MarketDataNotFoundError):
        facade.get_property_entity("prop-tw-001", principal=principal_beta)

    # Listing observation isolation
    listing_a = facade.get_listing_observation("list-obs-001", principal=principal_alpha)
    assert listing_a.listing_obs_id == "list-obs-001"

    with pytest.raises(MarketDataNotFoundError):
        facade.get_listing_observation("list-beta-001", principal=principal_alpha)


def test_m1_m2_authorization_engine_security_audit_events(facade, foreign_tenant_principal):
    """M1 & M2: Security denials and audited reads write canonical AuditEvents to AuthorizationEngine."""
    engine = AuthorizationEngine()
    facade_with_engine = MarketDataFacade(client=facade.client, auth_engine=engine, enforce_auth=True)

    # 1. Unauthenticated denial emits audit event
    with pytest.raises(MarketDataAuthorizationError):
        facade_with_engine.get_site_market_context("site-taipei-001", principal=ANONYMOUS)

    # 2. Cross-tenant denial emits audit event
    with pytest.raises(MarketDataAuthorizationError):
        facade_with_engine.get_site_market_context(
            "site-taipei-001",
            tenant_id="tenant-alpha",
            principal=foreign_tenant_principal,
        )

    # 3. Role unauthorized denial emits audit event
    unauth_role_principal = Principal(
        subject_id="intruder-1",
        roles=frozenset(),
        scope=Scope(tenant_id="tenant-alpha", clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )
    with pytest.raises(MarketDataAuthorizationError):
        facade_with_engine.get_site_market_context(
            "site-taipei-001",
            tenant_id="tenant-alpha",
            principal=unauth_role_principal,
        )

    # Assert all security denial events were recorded in audit_log
    audit_events = engine.audit_log.list_events()
    assert len(audit_events) >= 3
    for ev in audit_events:
        assert ev.event_type == "security.authorization"
        assert ev.outcome == "deny"


def test_t3_transport_explicit_requirement_and_rejection_of_silent_defaults(seeded_transport, expansion_principal):
    """T3: Verify DataPlatformClient and MarketDataFacade require explicit transport and reject silent in-memory production defaults."""
    # 1. DataPlatformClient() without transport raises DataPlatformClientError(missing_transport)
    with pytest.raises(DataPlatformClientError) as exc_info:
        DataPlatformClient()
    assert exc_info.value.code == "missing_transport"

    # 2. MarketDataFacade() without client or transport raises MarketDataFacadeError(missing_client)
    with pytest.raises(MarketDataFacadeError) as exc_info:
        MarketDataFacade()
    assert exc_info.value.code == "missing_client"

    # 3. Explicit transport passed to MarketDataFacade directly constructs and works
    facade_direct_transport = MarketDataFacade(transport=seeded_transport, enforce_auth=True)
    ctx = facade_direct_transport.get_site_market_context(
        "site-taipei-001",
        period_grain=PeriodGrain.MONTHLY,
        period_key="2026-08",
        tenant_id="tenant-alpha",
        principal=expansion_principal,
    )
    assert ctx.identity.site_id == "site-taipei-001"


