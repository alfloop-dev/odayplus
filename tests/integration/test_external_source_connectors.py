"""Integration tests for the external source connector completion (ODP-PV-003) and live listing feed adapter (ODP-EXT-002)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from modules.external_data.application.listing_feed_adapter import (
    ListingFeedClient,
    LiveListingFeedAdapter,
    TimeoutError,
    UnauthorizedError,
)
from modules.external_data.connectors import build_external_connectors
from modules.external_data.geo import GeocodeCandidate, GeoPipeline, StaticGeocodeProvider
from modules.integration.connectors import build_internal_connectors
from modules.listing.application.pipeline import ListingPipeline
from modules.listing.infrastructure.repositories import InMemoryListingRepository
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
    targets = {c.target for c in external.values()} | {c.target for c in internal.values()}
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
def test_invalid_records_quarantine_with_reasons(connectors: dict, contract_id: str) -> None:
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
    import h3

    assert address.h3_res_9 and h3.is_valid_cell(address.h3_res_9)
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
    import h3

    assert h3.is_valid_cell(cell.h3_index)


# --- acceptance #4: lineage envelope ----------------------------------------


def test_lineage_envelope_preserves_required_provenance(connectors: dict) -> None:
    records = _load("competitor_store_snapshot", "valid")["records"]
    run = connectors["competitor_store_snapshot"].ingest(records, ingestion_time=INGESTION_TIME)
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


# --- Listing Feed Adapter Tests (ODP-EXT-002) --------------------------------


def _get_geo_pipeline() -> GeoPipeline:
    return GeoPipeline(
        StaticGeocodeProvider(
            {
                "台北市大安區復興南路二段100號": GeocodeCandidate(
                    latitude=25.026,
                    longitude=121.543,
                    precision="rooftop",
                    confidence=0.92,
                    provider="fixture",
                    admin_city="台北市",
                    admin_district="大安區",
                )
            }
        )
    )


def test_success_contract_test(tmp_path) -> None:
    repository = InMemoryListingRepository()
    pipeline = ListingPipeline(repository=repository, geo_pipeline=_get_geo_pipeline())

    # We mock client to return valid fixture records on fetch_listings
    client = ListingFeedClient(api_url="mock://api", api_key="valid_token")
    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        snapshot_dir=str(tmp_path / "snapshots"),
        quarantine_dir=str(tmp_path / "quarantine"),
    )

    valid_fixture = json.loads(
        (FIXTURES_ROOT / "external" / "listing_raw_snapshot.valid.json").read_text(encoding="utf-8")
    )

    # Test process feed with a simulated response payload
    result = adapter.process_feed(replay_payload=valid_fixture)

    assert result["status"] == "success"
    assert result["accepted_count"] == 1  # LST-001 is active and ground floor (1F)
    assert result["rejected_count"] == 1  # LST-002 is stale status (only active becomes candidate)
    assert result["quarantined_count"] == 1
    assert len(repository.listings) == 2
    assert len(repository.candidates) == 1


def test_duplicate_contract_test(tmp_path) -> None:
    repository = InMemoryListingRepository()
    pipeline = ListingPipeline(repository=repository, geo_pipeline=_get_geo_pipeline())

    client = ListingFeedClient(api_url="mock://api", api_key="valid_token")
    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        snapshot_dir=str(tmp_path / "snapshots"),
        quarantine_dir=str(tmp_path / "quarantine"),
    )

    valid_fixture = json.loads(
        (FIXTURES_ROOT / "external" / "listing_raw_snapshot.valid.json").read_text(encoding="utf-8")
    )

    first_result = adapter.process_feed(replay_payload=valid_fixture)
    assert first_result["status"] == "success"

    # Second processing of the exact same payload should trigger the idempotency check
    second_result = adapter.process_feed(replay_payload=valid_fixture)
    assert second_result["status"] == "duplicate"
    assert "already been processed" in second_result["message"]


def test_malformed_payload_contract_test(tmp_path) -> None:
    repository = InMemoryListingRepository()
    pipeline = ListingPipeline(repository=repository, geo_pipeline=_get_geo_pipeline())

    client = ListingFeedClient(api_url="mock://api", api_key="valid_token")
    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        snapshot_dir=str(tmp_path / "snapshots"),
        quarantine_dir=str(tmp_path / "quarantine"),
    )

    invalid_fixture = json.loads(
        (FIXTURES_ROOT / "external" / "listing_raw_snapshot.invalid.json").read_text(
            encoding="utf-8"
        )
    )

    # We map the invalid fixture cases into a list of records
    records = [case["record"] for case in invalid_fixture["cases"]]
    malformed_payload = {
        "contract_id": "listing_raw_snapshot",
        "snapshot_id": "malformed-test-batch",
        "records": records,
    }

    result = adapter.process_feed(replay_payload=malformed_payload)

    assert result["status"] == "success"
    assert result["accepted_count"] == 0
    assert result["quarantined_count"] == 3  # All three cases are invalid and enter quarantine
    assert Path(result["quarantine_path"]).exists()

    quarantine_data = json.loads(Path(result["quarantine_path"]).read_text(encoding="utf-8"))
    assert len(quarantine_data) == 3
    assert quarantine_data[0]["status"] == "RAW"
    assert any(issue["code"] == "missing_required_field" for issue in quarantine_data[0]["issues"])


def test_unauthorized_contract_test(tmp_path) -> None:
    pipeline = ListingPipeline()
    # Trigger client auth failure check
    client = ListingFeedClient(
        api_url="mock://api", api_key="unauthorized_key"
    )  # pragma: allowlist-secret
    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        snapshot_dir=str(tmp_path / "snapshots"),
        quarantine_dir=str(tmp_path / "quarantine"),
    )

    with pytest.raises(UnauthorizedError) as exc_info:
        adapter.process_feed()

    assert (
        "Authentication failed" in str(exc_info.value)
        or "access denied" in str(exc_info.value).lower()
    )


def test_timeout_contract_test(tmp_path) -> None:
    pipeline = ListingPipeline()
    # Trigger client timeout failure check
    client = ListingFeedClient(api_url="mock://api", api_key="timeout_trigger")
    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        snapshot_dir=str(tmp_path / "snapshots"),
        quarantine_dir=str(tmp_path / "quarantine"),
    )

    with pytest.raises(TimeoutError) as exc_info:
        adapter.process_feed()

    assert "timed out" in str(exc_info.value).lower()


def test_fixture_compatible_replay(tmp_path) -> None:
    # Verify we can replay files correctly and it remains fully compatible
    repository = InMemoryListingRepository()
    pipeline = ListingPipeline(repository=repository, geo_pipeline=_get_geo_pipeline())

    client = ListingFeedClient(api_url="mock://api", api_key="fixture_default")
    adapter = LiveListingFeedAdapter(
        client=client,
        pipeline=pipeline,
        snapshot_dir=str(tmp_path / "snapshots"),
        quarantine_dir=str(tmp_path / "quarantine"),
    )

    valid_fixture = json.loads(
        (FIXTURES_ROOT / "external" / "listing_raw_snapshot.valid.json").read_text(encoding="utf-8")
    )
    result = adapter.process_feed(replay_payload=valid_fixture)

    assert result["status"] == "success"
    assert result["snapshot_id"] == "listing-2026-06-26"
    assert Path(result["raw_snapshot_path"]).exists()
    assert Path(result["canonical_snapshot_path"]).exists()
