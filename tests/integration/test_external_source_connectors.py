"""Integration tests for the external source connector completion (ODP-PV-003).

These exercise the connector framework end-to-end against the golden fixtures
and prove the four acceptance capabilities:

  * source-to-canonical mapping (typed canonical entities + identity);
  * the data-quality gate (invalid records quarantine with ODP-DATA-05 reasons);
  * geocode / H3 enrichment for address-bearing external sources; and
  * the lineage envelope (source id, observation / ingestion time, schema
    version, field lineage, quarantine reason) preserved on every record.

The connector matrix is also asserted to cover every source category required by
the product-grade E2E wave (store, transaction, machine, maintenance, customer
service, pricing, listing, POI, competitor, admin boundary, geocode).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from modules.external_data.connectors import build_external_connectors
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.integration.connectors import build_internal_connectors
from shared.domain import AddressLocation, CompetitorStore, GeoCell, Listing, Poi

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "source_data"
INGESTION_TIME = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)

# Deterministic geocoder covering every address used by the external fixtures.
_GEOCODE_TABLE = {
    "台北市信義區忠孝東路五段": (25.040944, 121.565472, "台北市", "信義區", 0.95),
    "台北市信義區松仁路50號": (25.035, 121.567, "台北市", "信義區", 0.7),
    "台北市大安區復興南路二段100號": (25.026, 121.543, "台北市", "大安區", 0.92),
    "台北市大安區復興南路二段200號": (25.028, 121.545, "台北市", "大安區", 0.94),
}


def _geo_pipeline() -> GeoPipeline:
    candidates = {
        address: GeocodeCandidate(
            latitude=lat,
            longitude=lng,
            precision="rooftop",
            confidence=conf,
            provider="fixture",
            admin_city=city,
            admin_district=district,
        )
        for address, (lat, lng, city, district, conf) in _GEOCODE_TABLE.items()
    }
    return GeoPipeline(StaticGeocodeProvider(candidates))


def _load(contract_id: str, kind: str) -> dict:
    path = FIXTURES_ROOT / "external" / f"{contract_id}.{kind}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def connectors() -> dict:
    return build_external_connectors(geo_pipeline=_geo_pipeline())


# --- coverage: connector matrix spans every required source category --------


def test_connector_matrix_covers_all_required_sources() -> None:
    external = build_external_connectors(geo_pipeline=_geo_pipeline())
    internal = build_internal_connectors()
    targets = {c.target for c in external.values()} | {
        c.target for c in internal.values()
    }
    # store / transaction / machine / maintenance / customer service / pricing
    for canonical in (
        "store",
        "transaction",
        "machine",
        "work_order",
        "customer_service_case",
        "price_schedule",
    ):
        assert canonical in targets, f"internal source {canonical} not covered"
    # listing / POI / competitor / admin boundary / geocode
    for canonical in (
        "listing",
        "poi",
        "competitor_store",
        "geo_cell",
        "address_location",
    ):
        assert canonical in targets, f"external source {canonical} not covered"


# --- acceptance #1: source-to-canonical -------------------------------------


def test_poi_source_to_canonical_is_typed_and_deterministic(connectors: dict) -> None:
    records = _load("poi_snapshot", "valid")["records"]
    run = connectors["poi_snapshot"].ingest(records, ingestion_time=INGESTION_TIME)

    assert run.accepted_count == len(records)
    poi = run.accepted[0].canonical
    assert isinstance(poi, Poi)
    assert poi.source_poi_id == "POI-001"
    assert poi.poi_id  # deterministic canonical id assigned

    # Re-running yields identical canonical ids (deterministic identity).
    rerun = connectors["poi_snapshot"].ingest(records, ingestion_time=INGESTION_TIME)
    assert [r.canonical.poi_id for r in run.accepted] == [
        r.canonical.poi_id for r in rerun.accepted
    ]


def test_competitor_and_listing_map_to_canonical(connectors: dict) -> None:
    competitor_records = _load("competitor_store_snapshot", "valid")["records"]
    crun = connectors["competitor_store_snapshot"].ingest(competitor_records)
    competitor = crun.accepted[0].canonical
    assert isinstance(competitor, CompetitorStore)
    assert competitor.store_name == "潔衣家信義店"
    assert competitor.competitor_store_id

    listing_records = _load("listing_raw_snapshot", "valid")["records"]
    lrun = connectors["listing_raw_snapshot"].ingest(listing_records)
    listing = lrun.accepted[0].canonical
    assert isinstance(listing, Listing)
    assert listing.rent_amount > 0
    assert listing.source_id == "SRC-EXT-LISTING-PARTNER"
    assert listing.address_id  # linked to a geocoded address


# --- acceptance #2: data-quality gate / quarantine --------------------------


@pytest.mark.parametrize(
    "contract_id",
    [
        "poi_snapshot",
        "competitor_store_snapshot",
        "listing_raw_snapshot",
        "admin_boundary_snapshot",
        "geocode_result_snapshot",
    ],
)
def test_invalid_records_quarantine_with_reasons(
    connectors: dict, contract_id: str
) -> None:
    cases = _load(contract_id, "invalid")["cases"]
    records = [case["record"] for case in cases]
    run = connectors[contract_id].ingest(records, ingestion_time=INGESTION_TIME)

    assert run.accepted_count == 0
    assert run.quarantined_count == len(records)
    for record in run.quarantined:
        assert record.canonical is None
        assert record.lineage.quarantine_reasons, "quarantined record needs a reason"
        assert record.issues


def test_quarantine_reasons_map_to_canonical_taxonomy(connectors: dict) -> None:
    cases = _load("poi_snapshot", "invalid")["cases"]
    run = connectors["poi_snapshot"].ingest([c["record"] for c in cases])
    assert run.quarantine_reasons() <= {
        "missing_required_field",
        "schema_mismatch",
        "invalid_time",
        "invalid_amount",
    }
    assert "missing_required_field" in run.quarantine_reasons()


# --- acceptance #3: geocode / H3 enrichment ---------------------------------


def test_geocode_connector_emits_address_with_h3(connectors: dict) -> None:
    records = _load("geocode_result_snapshot", "valid")["records"]
    run = connectors["geocode_result_snapshot"].ingest(records)
    address = run.accepted[0].canonical
    assert isinstance(address, AddressLocation)
    assert address.h3_res_9 and address.h3_res_9.startswith("h3r9_")
    assert run.accepted[0].geocode.h3_resolution_map[9] == address.h3_res_9


def test_poi_enrichment_attaches_geo_cell_and_h3(connectors: dict) -> None:
    records = _load("poi_snapshot", "valid")["records"]
    run = connectors["poi_snapshot"].ingest(records)
    enriched = run.accepted[0]
    assert enriched.geocode is not None
    assert enriched.geocode.h3_resolution_map  # H3 cells computed
    assert enriched.canonical.geo_cell_id.startswith("geo-cell:")


def test_admin_boundary_canonicalizes_to_geo_cell(connectors: dict) -> None:
    records = _load("admin_boundary_snapshot", "valid")["records"]
    run = connectors["admin_boundary_snapshot"].ingest(records)
    cell = run.accepted[0].canonical
    assert isinstance(cell, GeoCell)
    assert cell.admin_district == "信義區"
    assert cell.h3_index.startswith("h3r9_")


# --- acceptance #4: lineage envelope ----------------------------------------


def test_lineage_envelope_preserves_required_provenance(connectors: dict) -> None:
    records = _load("competitor_store_snapshot", "valid")["records"]
    run = connectors["competitor_store_snapshot"].ingest(
        records, ingestion_time=INGESTION_TIME
    )
    lineage = run.accepted[0].lineage

    assert lineage.source_record_id == "CMP-001"  # source id preserved
    assert lineage.source_system == "SRC-EXT-COMPETITOR"
    assert lineage.observation_time is not None  # from last_verified_at
    assert lineage.ingestion_time == INGESTION_TIME
    assert lineage.schema_version  # contract / registry schema version
    assert lineage.mapping_id == "MAP-EXT-COMPETITOR-v1"
    assert lineage.field_lineage  # field-level provenance recorded
    assert lineage.quarantine_reasons == ()


def test_quarantined_record_lineage_carries_reason(connectors: dict) -> None:
    cases = _load("geocode_result_snapshot", "invalid")["cases"]
    run = connectors["geocode_result_snapshot"].ingest(
        [c["record"] for c in cases], ingestion_time=INGESTION_TIME
    )
    rejected = run.quarantined[0]
    assert rejected.lineage.quarantine_reasons
    assert rejected.lineage.ingestion_time == INGESTION_TIME
    assert rejected.lineage.schema_version == "geocode-v1"


def test_schema_version_comes_from_contract_when_declared(connectors: dict) -> None:
    run = connectors["admin_boundary_snapshot"].ingest(
        _load("admin_boundary_snapshot", "valid")["records"]
    )
    assert run.accepted[0].lineage.schema_version == "admin-boundary-v1"
