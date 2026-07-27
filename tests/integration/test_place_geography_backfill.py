"""PostgreSQL 16 integration tests for the live-provider geography backfill.

ODP-PRODUCTION-GEOGRAPHY-BACKFILL-001. The suite installs the real
``apps/data_platform/sql/control_schema.sql`` into a throwaway PostgreSQL 16
database and drives ``PlaceGeographyBackfill`` with stub provider transports
(the production CLI refuses non-live modes; stubs here only replace the HTTP
layer while every persistence and validation path is the production code).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

import pytest

from apps.data_platform.geography_backfill import (
    PlaceGeographyBackfill,
    canonical_content_sha256,
)
from modules.external_data.geo.pipeline import stable_h3_index
from modules.external_data.providers.live import _candidate_from_geocode_payload

pytestmark = pytest.mark.requires_live_env

NAMESPACE = UUID("0d1f11d6-32b3-49a2-a2c7-1c1a53d7c001")
CONTROL_SCHEMA_SQL = (
    Path(__file__).parents[2] / "apps/data_platform/sql/control_schema.sql"
).read_text(encoding="utf-8")

TENANT_A = uuid5(NAMESPACE, "tenant-a")
TENANT_B = uuid5(NAMESPACE, "tenant-b")
STORE_A = uuid5(NAMESPACE, "store-a")
STORE_B = uuid5(NAMESPACE, "store-b")
STORE_NO_ADDRESS = uuid5(NAMESPACE, "store-no-address")
ADDRESS_A = uuid5(NAMESPACE, "address-a")
ADDRESS_B = uuid5(NAMESPACE, "address-b")

ADDRESS_A_RAW = "432台灣台中市大肚區沙田路二段132巷43弄3號"
ADDRESS_B_RAW = "900台灣屏東縣屏東市仁愛路90號"

OBSERVED_AT = "2026-07-27T10:00:00+00:00"


def _geocode_payload(
    *,
    latitude: float,
    longitude: float,
    city: str,
    district: str,
    confidence: float = 0.97,
    request_id: str = "place-id-1",
) -> dict:
    return {
        "result": {
            "latitude": latitude,
            "longitude": longitude,
            "confidence": confidence,
            "precision": "rooftop",
            "provider_id": "geocode.primary_api",
            "city": city,
            "district": district,
        },
        "request_id": request_id,
        "observed_at": OBSERVED_AT,
        "upstream": "google-maps-geocoding",
    }


class StubGeocodeProvider:
    """Replays canned live-shaped payloads through the production parser."""

    correlation_id = "test-correlation"

    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls = 0

    def lookup_with_payload(self, normalized_address):
        self.calls += 1
        payload = self.payloads.get(normalized_address.normalized_address, {})
        candidate = _candidate_from_geocode_payload(
            payload, normalized_address, "geocode.primary_api"
        )
        return candidate, payload


class StubDatasetProvider:
    """Mirrors the governed gateway: one snapshot id per dataset day whose
    records carry a fresh ``observed_at`` / ``event_time`` on every fetch."""

    def __init__(self, *, snapshot_id: str, provider_id: str, contract_id: str, records):
        self.snapshot_id = snapshot_id
        self.provider_id = provider_id
        self.contract_id = contract_id
        self.base_records = records
        self.fetches = 0

    def fetch_and_ingest(self):
        self.fetches += 1
        now = datetime(2026, 7, 27, 10, self.fetches, tzinfo=UTC)
        stamp = now.isoformat()
        records = [
            dict(record, snapshot_id=self.snapshot_id, observed_at=stamp, event_time=stamp)
            for record in self.base_records
        ]
        return SimpleNamespace(
            raw_snapshot=SimpleNamespace(
                snapshot_id=self.snapshot_id,
                provider_id=self.provider_id,
                source_contract_id=self.contract_id,
                correlation_id="test-correlation",
                records=tuple(records),
                checksum_sha256=canonical_content_sha256({"records": records}),
                observed_at=now,
                fetched_at=now,
            )
        )


def _admin_provider() -> StubDatasetProvider:
    return StubDatasetProvider(
        snapshot_id="adminboundary-test-0001",
        provider_id="admin_boundary.official_dataset",
        contract_id="admin_boundary_snapshot",
        records=[
            {"admin_level": "city", "admin_name": "台中市", "parent_admin_code": ""},
            {"admin_level": "district", "admin_name": "大肚區", "parent_admin_code": "台中市"},
            {"admin_level": "city", "admin_name": "屏東縣", "parent_admin_code": ""},
            {"admin_level": "district", "admin_name": "屏東市", "parent_admin_code": "屏東縣"},
        ],
    )


def _poi_provider() -> StubDatasetProvider:
    return StubDatasetProvider(
        snapshot_id="poi-test-0001",
        provider_id="poi.commercial_api",
        contract_id="poi_snapshot",
        records=[
            {
                "source_poi_id": "poi-1",
                "poi_name": "測試便利商店",
                "latitude": 24.1394,
                "longitude": 120.5508,
            }
        ],
    )


def _default_payloads() -> dict[str, dict]:
    a = _geocode_payload(
        latitude=24.1394693,
        longitude=120.5508707,
        city="臺中市",
        district="大肚區",
        request_id="place-id-a",
    )
    b = _geocode_payload(
        latitude=22.6763,
        longitude=120.4929,
        city="屏東縣",
        district="屏東市",
        request_id="place-id-b",
    )
    from modules.external_data.geo.pipeline import normalize_address

    return {
        normalize_address(ADDRESS_A_RAW).normalized_address: a,
        normalize_address(ADDRESS_B_RAW).normalized_address: b,
    }


@pytest.fixture
def geography_db(intake_blank_db):
    connection = intake_blank_db.connect(autocommit=False)
    connection.execute(
        """
        CREATE SCHEMA core;
        CREATE TABLE core.tenants (tenant_id UUID PRIMARY KEY);
        CREATE TABLE core.machines (machine_id UUID PRIMARY KEY);
        CREATE TABLE core.machine_status_events (status_event_id UUID PRIMARY KEY);
        CREATE TABLE core.transactions (transaction_id UUID PRIMARY KEY);
        CREATE TABLE core.address_locations (
            address_id UUID PRIMARY KEY,
            raw_address TEXT
        );
        CREATE TABLE core.stores (
            store_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
            source_store_id TEXT NOT NULL,
            address_id UUID REFERENCES core.address_locations(address_id),
            is_current BOOLEAN NOT NULL DEFAULT TRUE
        );
        """
    )
    connection.execute(CONTROL_SCHEMA_SQL.replace("{{control_schema}}", "data_plane"))
    for tenant in (TENANT_A, TENANT_B):
        connection.execute("INSERT INTO core.tenants VALUES (%s)", (tenant,))
    connection.execute(
        "INSERT INTO core.address_locations VALUES (%s, %s), (%s, %s)",
        (ADDRESS_A, ADDRESS_A_RAW, ADDRESS_B, ADDRESS_B_RAW),
    )
    connection.execute(
        "INSERT INTO core.stores VALUES (%s, %s, %s, %s, TRUE)",
        (STORE_A, TENANT_A, "source-place-a", ADDRESS_A),
    )
    connection.execute(
        "INSERT INTO core.stores VALUES (%s, %s, %s, %s, TRUE)",
        (STORE_B, TENANT_B, "source-place-b", ADDRESS_B),
    )
    connection.execute(
        "INSERT INTO core.stores VALUES (%s, %s, %s, NULL, TRUE)",
        (STORE_NO_ADDRESS, TENANT_B, "source-place-empty"),
    )
    connection.commit()
    yield connection
    connection.close()


def _engine(connection, payloads: dict[str, dict] | None = None) -> PlaceGeographyBackfill:
    return PlaceGeographyBackfill(
        connection,
        geocode_provider=StubGeocodeProvider(payloads or _default_payloads()),
        admin_boundary_provider=_admin_provider(),
        poi_provider=_poi_provider(),
        rate_limit_per_second=0.0,
        clock=lambda: datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
    )


def _rows(connection, sql: str, params=()):
    cursor = connection.execute(sql, params)
    return cursor.fetchall()


def test_backfill_projects_live_geography_with_evidence_and_lineage(geography_db):
    report = _engine(geography_db).run()

    assert report.inserted == 2
    assert report.unchanged == 0
    assert report.skipped_no_address == 1
    assert report.quarantined == {}
    assert report.reconciled and report.partition_complete
    assert report.admin_snapshot_id == "adminboundary-test-0001"
    assert report.poi_snapshot_id == "poi-test-0001"

    rows = _rows(
        geography_db,
        """
        SELECT store_id, city, district, latitude, longitude, geocode_confidence,
               h3_res_9, h3_derivation_version, valid_from, observed_at
        FROM data_plane.place_geography ORDER BY city
        """,
    )
    assert len(rows) == 2
    by_store = {row[0]: row for row in rows}
    row_a = by_store[STORE_A]
    assert row_a[1] == "台中市" and row_a[2] == "大肚區"
    assert float(row_a[3]) == pytest.approx(24.1394693)
    assert row_a[6] == stable_h3_index(24.1394693, 120.5508707, 9)
    assert row_a[7] == "stable_h3_index-v1"
    assert row_a[8] == row_a[9] == datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    assert float(row_a[5]) == pytest.approx(0.97)

    evidence = _rows(
        geography_db,
        """
        SELECT provider_id, provider_request_id, admin_match, content_sha256,
               response_payload->'result'->>'latitude'
        FROM data_plane.geography_provider_snapshots
        WHERE store_id = %s
        """,
        (STORE_A,),
    )
    assert len(evidence) == 1
    assert evidence[0][0] == "geocode.primary_api"
    assert evidence[0][1] == "place-id-a"
    assert evidence[0][2] is True
    assert len(evidence[0][3]) == 64
    assert float(evidence[0][4]) == pytest.approx(24.1394693)

    lineage = _rows(
        geography_db,
        """
        SELECT tenant_id, canonical_id FROM data_plane.canonical_lineage
        WHERE canonical_table = 'data_plane.place_geography' ORDER BY tenant_id
        """,
    )
    assert [(row[0], row[1]) for row in lineage] == sorted(
        [(TENANT_A, STORE_A), (TENANT_B, STORE_B)]
    )

    runs = _rows(
        geography_db,
        """
        SELECT status, source_kind, processed_count, valid_loaded, quarantined_count,
               reconciled, partition_complete
        FROM data_plane.ingestion_runs
        """,
    )
    assert runs == [("SUCCEEDED", "place_geography", 2, 2, 0, True, True)]

    datasets = _rows(
        geography_db,
        "SELECT snapshot_id, record_count, h3_index_map FROM data_plane.geo_dataset_snapshots "
        "ORDER BY snapshot_id",
    )
    assert [row[0] for row in datasets] == ["adminboundary-test-0001", "poi-test-0001"]
    poi_map = datasets[1][2]
    assert poi_map == {"poi-1": stable_h3_index(24.1394, 120.5508, 9)}


def test_replay_of_identical_provider_content_is_idempotent(geography_db):
    first = _engine(geography_db).run()
    second_engine = _engine(geography_db)
    # A later same-day fetch keeps the dataset snapshot id but refreshes the
    # volatile observed_at / event_time stamps, like the governed gateway.
    second_engine._admin.fetches = 7
    second_engine._poi.fetches = 7
    second = second_engine.run()

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.unchanged == 2
    assert second.quarantined == {}
    assert second.reconciled
    assert second.admin_snapshot_id == first.admin_snapshot_id

    counts = _rows(
        geography_db,
        """
        SELECT
            (SELECT count(*) FROM data_plane.place_geography),
            (SELECT count(*) FROM data_plane.geography_provider_snapshots),
            (SELECT count(*) FROM data_plane.canonical_lineage),
            (SELECT count(*) FROM data_plane.quarantined_records),
            (SELECT count(*) FROM data_plane.geo_dataset_snapshots)
        """,
    )
    assert counts == [(2, 2, 2, 0, 2)]
    # The first capture remains the immutable dataset snapshot of record.
    stored = _rows(
        geography_db,
        "SELECT observed_at FROM data_plane.geo_dataset_snapshots ORDER BY snapshot_id",
    )
    assert all(row[0] == datetime(2026, 7, 27, 10, 1, tzinfo=UTC) for row in stored)


def test_conflicting_observation_is_quarantined_not_overwritten(geography_db):
    _engine(geography_db).run()

    moved = _default_payloads()
    key = next(iter(k for k in moved if "大肚" in k))
    moved[key] = _geocode_payload(
        latitude=24.20,
        longitude=120.60,
        city="臺中市",
        district="大肚區",
        request_id="place-id-a-moved",
    )
    report = _engine(geography_db, moved).run()

    assert report.inserted == 0
    assert report.unchanged == 1
    assert report.quarantined == {"GEOGRAPHY_CONFLICT": 1}
    assert report.reconciled

    canonical = _rows(
        geography_db,
        "SELECT latitude FROM data_plane.place_geography WHERE store_id = %s",
        (STORE_A,),
    )
    assert len(canonical) == 1
    assert float(canonical[0][0]) == pytest.approx(24.1394693)

    quarantine = _rows(
        geography_db,
        "SELECT reason_code, source_id FROM data_plane.quarantined_records",
    )
    assert quarantine == [("GEOGRAPHY_CONFLICT", "source-place-a")]
    # The conflicting live observation still has immutable evidence.
    evidence_count = _rows(
        geography_db,
        "SELECT count(*) FROM data_plane.geography_provider_snapshots WHERE store_id = %s",
        (STORE_A,),
    )
    assert evidence_count == [(2,)]


def test_admin_mismatch_and_out_of_market_are_quarantined(geography_db):
    payloads = _default_payloads()
    keys = sorted(payloads)
    key_a = next(k for k in keys if "大肚" in k)
    key_b = next(k for k in keys if "屏東" in k)
    payloads[key_a] = _geocode_payload(
        latitude=24.1394693,
        longitude=120.5508707,
        city="不存在市",
        district="不存在區",
    )
    payloads[key_b] = _geocode_payload(
        latitude=35.68,
        longitude=139.76,
        city="屏東縣",
        district="屏東市",
    )
    report = _engine(geography_db, payloads).run()

    assert report.inserted == 0
    assert report.quarantined == {
        "ADMIN_BOUNDARY_MISMATCH": 1,
        "COORDINATES_OUT_OF_MARKET": 1,
    }
    assert _rows(geography_db, "SELECT count(*) FROM data_plane.place_geography") == [(0,)]
    flags = _rows(
        geography_db,
        "SELECT admin_match FROM data_plane.geography_provider_snapshots ORDER BY store_id",
    )
    assert all(row[0] is False for row in flags)
