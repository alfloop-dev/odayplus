from __future__ import annotations

import hashlib
import json
import re

import pytest

from modules.listing.domain.models import CandidateSiteDraft
from product_ops.migrations.assisted_listing_intake.migrate import IntakeMigrator, ensure_uuid
from shared.domain.models import AddressLocation, CandidateSite, Listing

# Mark all tests as requiring a live PostgreSQL 16 server
pytestmark = pytest.mark.requires_live_env


@pytest.fixture(scope="module")
def migration_pg_server(tmp_path_factory):
    """Always mint a local cluster; never use an application/environment DSN."""
    import pgserver
    import psycopg

    from tests.conftest import IntakePgServer, _install_pgcrypto_stub

    _install_pgcrypto_stub(pgserver)
    directory = tmp_path_factory.mktemp("migration-input-pg")
    server = pgserver.get_server(directory)
    host = re.search(r"host=([^&]+)", server.get_uri()).group(1)
    try:
        yield IntakePgServer(psycopg=psycopg, admin_params={"host": host, "dbname": "postgres", "user": "postgres"})
    finally:
        server.cleanup()


@pytest.fixture
def intake_blank_db(migration_pg_server):
    from tests.conftest import IntakeDatabase

    name = migration_pg_server.create_database()
    try:
        yield IntakeDatabase(server=migration_pg_server, dbname=name)
    finally:
        # A leaked connection fails cleanup instead of being terminated.
        with migration_pg_server.connect("postgres") as connection:
            connection.execute(f'DROP DATABASE "{name}"')


def synthetic_history(intake_id, raw=None):
    """Explicit synthetic source facts for positive fixtures, not runtime defaults."""
    raw = {"synthetic_intake": intake_id} if raw is None else raw
    return {
        "submittedBy": "10000000-0000-0000-0000-000000000001",
        "submittedAt": "2026-07-14T06:10:00Z", "intakeMethod": "URL", "stage": "READY",
        "version": 7, "rawSnapshot": raw,
        "rawObjectUri": f"odp-artifact://synthetic/{intake_id}",
        "rawSnapshotSha256": hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "capturedAt": "2026-07-14T06:11:00Z", "observedAt": "2026-07-14T06:11:00Z",
        "captureMethod": "SERVER_RETRIEVAL", "mediaType": "application/json", "objectGeneration": 123,
        "residencyMode": "TW_ONLY",
        "retentionClass": "STANDARD", "encryptionKeyRef": "kms://synthetic-key",
        "parserVersion": "1.4", "parserRunStatus": "SUCCEEDED",
    }


@pytest.fixture(name="conn")
def migration_connection(intake_blank_db):
    """Close the caller-owned connection before its database is dropped."""
    connection = intake_blank_db.connect()
    try:
        yield connection
    finally:
        connection.close()


def test_migration_schema_upgrade_and_rollback(conn) -> None:
    migrator = IntakeMigrator(conn)

    # 1. Apply schema and verify tables are created
    migrator.apply_schema()

    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'intake'")
    tables = {r[0] for r in cur.fetchall()}
    assert "intakes" in tables
    assert "source_snapshots" in tables
    assert "parser_runs" in tables

    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'identity'")
    identity_tables = {r[0] for r in cur.fetchall()}
    assert "properties" in identity_tables
    assert "source_identity_edges" in identity_tables

    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'expansion'")
    expansion_tables = {r[0] for r in cur.fetchall()}
    assert "listings" in expansion_tables
    assert "listing_revisions" in expansion_tables
    assert "promotion_decisions" in expansion_tables

    # 2. Rollback schema and verify tables are dropped
    migrator.rollback_schema()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'intake'")
    tables_after = {r[0] for r in cur.fetchall()}
    assert len(tables_after) == 0


def test_backfill_happy_path(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intakes = [
        {
            "id": "IN-2024",
            "tenantId": tenant_id,
            "sourceId": "SRC-591",
            "originalUrl": "https://591.com.tw/detail-2024.html",
            "canonicalUrl": "https://591.com.tw/detail-2024.html",
            **synthetic_history("IN-2024", {"html": "dummy raw data"}),
            "rawObjectUri": "odp-artifact://snapshots/snap-2024",
            "sourcePolicyState": "APPROVED_RETRIEVAL",
            "stage": "READY",
            "heatZoneId": ensure_uuid("HZ-01"),
            "correlationId": "corr-2024",
            "submittedAt": "2026-07-14T06:10:00Z",
            "rawSnapshot": {"html": "dummy raw data"},
            "parsedFields": {"rent": 58000, "address": "台北市信義區松仁路 96 號 1F"},
            "matchResult": {
                "outcome": "NEW",
                "confidence": 1.0,
                "targetListingId": "L-2024",
            },
        }
    ]

    listing = Listing(
        listing_id="L-2024",
        source_listing_id="s591-2024",
        source_id="SRC-591",
        listing_status="new",
        rent_amount=58000.0,
        area_ping=18.0,
        floor="1F",
        snapshot_id="https://591.com.tw/detail-2024.html",
    )
    address = AddressLocation(
        address_id="ADDR-L-2024",
        raw_address="台北市信義區松仁路 96 號 1F",
        normalized_address="台北市信義區松仁路 96 號 1F",
        latitude=25.0330,
        longitude=121.5654,
        geocode_confidence=0.94,
    )
    candidate = CandidateSite(
        candidate_site_id="CS-1001",
        listing_id="L-2024",
        address_id="ADDR-L-2024",
        site_status="new",
    )
    candidate_draft = CandidateSiteDraft(
        listing=listing,
        address=address,
        candidate_site=candidate,
        heat_zone_id="HZ-01",
        status="CANDIDATE",
    )

    legacy_listings = [listing]
    legacy_candidates = [candidate_draft]

    res = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=legacy_listings,
        legacy_candidates=legacy_candidates,
        tenant_id=tenant_id,
        parser_release=parser_provenance(artifact_uri="gs://parser-artifacts/v1.4.tar.gz"),
    )

    assert res["counts"]["intakes_processed"] == 1
    assert res["counts"]["listings_processed"] == 1
    assert res["counts"]["candidates_processed"] == 1
    assert res["counts"]["findings"] == 0

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))

    cur.execute("SELECT original_url, processing_state FROM intake.intakes")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "https://591.com.tw/detail-2024.html"
    assert rows[0][1] == "READY"

    cur.execute("SELECT raw_object_uri FROM intake.source_snapshots")
    snaps = cur.fetchall()
    assert len(snaps) == 1
    assert snaps[0][0] == "odp-artifact://snapshots/snap-2024"

    cur.execute("SELECT decision_type, status FROM expansion.promotion_decisions")
    proms = cur.fetchall()
    assert len(proms) == 1
    assert proms[0][0] == "LEGACY_RECONCILED"
    assert proms[0][1] == "COMPLETED"

    verify_res = migrator.verify_shadow_comparison(tenant_id)
    assert verify_res["intake_count"] == 1
    assert verify_res["listing_count"] == 1
    assert verify_res["candidate_count"] == 1
    assert verify_res["open_findings"] == 0
    assert verify_res["shadow_comparison_success"] is True


def test_b1_property_identity_preservation_and_geocodes(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    listing1 = Listing(listing_id="L-1", source_listing_id="s1", source_id="SRC-591", rent_amount=30000.0)
    addr1 = AddressLocation(address_id="A-1", normalized_address="台北市信義區松仁路 96 號 1F", latitude=25.0330, longitude=121.5654)
    cand1 = CandidateSite(candidate_site_id="CS-1", listing_id="L-1", address_id="A-1")
    draft1 = CandidateSiteDraft(listing=listing1, address=addr1, candidate_site=cand1)

    listing2 = Listing(listing_id="L-2", source_listing_id="s2", source_id="SRC-591", rent_amount=45000.0)
    addr2 = AddressLocation(address_id="A-2", normalized_address="台北市大安區新生南路一段 100 號 2F", latitude=25.0400, longitude=121.5300)
    cand2 = CandidateSite(candidate_site_id="CS-2", listing_id="L-2", address_id="A-2")
    draft2 = CandidateSiteDraft(listing=listing2, address=addr2, candidate_site=cand2)

    migrator.backfill(
        legacy_intakes=[],
        legacy_listings=[listing1, listing2],
        legacy_candidates=[draft1, draft2],
        tenant_id=tenant_id,
    )

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT normalized_address, latitude, longitude FROM identity.properties")
    props = cur.fetchall()

    assert len(props) == 2
    addresses = {p[0] for p in props}
    assert "台北市信義區松仁路 96 號 1F" in addresses
    assert "台北市大安區新生南路一段 100 號 2F" in addresses

    lats = {float(p[1]) for p in props}
    assert 25.0330 in lats
    assert 25.0400 in lats


def test_b1_probe_null_coordinates_and_no_fabricated_identities(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    # Address with NO latitude or longitude
    listing = Listing(listing_id="L-NO-COORDS", source_listing_id="s-nocoords", source_id="SRC-591")
    addr = AddressLocation(address_id="A-NO-COORDS", normalized_address="台北市中山區南京東路三段 2 號", latitude=None, longitude=None)
    cand = CandidateSite(candidate_site_id="CS-NO-COORDS", listing_id="L-NO-COORDS", address_id="A-NO-COORDS")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    migrator.backfill(
        legacy_intakes=[],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
    )

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT normalized_address, latitude, longitude FROM identity.properties")
    props = cur.fetchall()

    assert len(props) == 1
    assert props[0][0] == "台北市中山區南京東路三段 2 號"
    assert props[0][1] is None
    assert props[0][2] is None

    for p in props:
        assert not p[0].startswith("UNKNOWN_ADDRESS_")
        assert not p[0].startswith("REDIRECTED_PROPERTY_")


def test_b2_month_and_source_partition_filtering(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-JULY",
        **synthetic_history("IN-JULY"),
        "originalUrl": "https://591.com.tw/detail-july.html",
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "submittedAt": "2026-07-14T00:00:00Z",
        "matchResult": {"targetListingId": "L-JULY"},
    }
    listing = Listing(listing_id="L-JULY", source_listing_id="s-july", source_id="SRC-591")
    addr = AddressLocation(address_id="A-JULY", normalized_address="台北市信義區松仁路 96 號 1F")
    cand = CandidateSite(candidate_site_id="CS-JULY", listing_id="L-JULY", address_id="A-JULY")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    res_1999 = migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
        month="1999-01",
    )
    assert res_1999["counts"]["intakes_processed"] == 0
    assert res_1999["counts"]["listings_processed"] == 0
    assert res_1999["counts"]["candidates_processed"] == 0

    res_broker = migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
        source_id="SRC-BROKER",
    )
    assert res_broker["counts"]["candidates_processed"] == 0
    assert res_broker["counts"]["findings"] == 0


def test_b3_shadow_comparison_count_and_checksum_proof(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-1",
        **synthetic_history("IN-1"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-1.html",
        "submittedAt": "2026-07-14T06:10:00Z",
        "matchResult": {"targetListingId": "L-1"},
    }
    listing = Listing(listing_id="L-1", source_listing_id="s1", source_id="SRC-591")
    addr = AddressLocation(address_id="A-1", normalized_address="台北市信義區松仁路 96 號 1F")
    cand = CandidateSite(candidate_site_id="CS-1", listing_id="L-1", address_id="A-1")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
    )

    verify_before = migrator.verify_shadow_comparison(tenant_id)
    assert verify_before["shadow_comparison_success"] is True

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("DELETE FROM expansion.candidate_sites WHERE tenant_id = %s", (tenant_id,))

    verify_after = migrator.verify_shadow_comparison(tenant_id)
    assert verify_after["shadow_comparison_success"] is False
    assert verify_after["blocking_findings"] >= 1


def test_b3_probe_shadow_proof_persistence_on_fresh_migrator(conn) -> None:
    migrator1 = IntakeMigrator(conn)
    migrator1.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-FRESH",
        **synthetic_history("IN-FRESH"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-fresh.html",
        "submittedAt": "2026-07-14T06:10:00Z",
        "matchResult": {"targetListingId": "L-FRESH"},
    }
    listing = Listing(listing_id="L-FRESH", source_listing_id="s-fresh", source_id="SRC-591")
    addr = AddressLocation(address_id="A-FRESH", normalized_address="台北市信義區松仁路 96 號 1F")
    cand = CandidateSite(candidate_site_id="CS-FRESH", listing_id="L-FRESH", address_id="A-FRESH")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    migrator1.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
    )

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("DELETE FROM expansion.candidate_sites WHERE tenant_id = %s", (tenant_id,))

    migrator2 = IntakeMigrator(conn)
    verify_res = migrator2.verify_shadow_comparison(tenant_id)

    assert verify_res["candidate_count"] == 0
    assert verify_res["shadow_comparison_success"] is False
    assert verify_res["blocking_findings"] >= 1


def test_b4_snapshot_provenance_and_policy_preservation(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-PROV",
        **synthetic_history("IN-PROV", {"html": "custom snapshot text"}),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-prov.html",
        "rawObjectUri": "gs://verified-bucket/snapshots/raw-100",
        "sourcePolicyState": "AUTH_REQUIRED",
        "rawSnapshot": {"html": "custom snapshot text"},
    }

    migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        parser_release=parser_provenance(
            semantic_version="2.0", artifact_uri="gs://verified-parser-artifacts/v2.0.tar.gz", artifact_sha256="b" * 64,
        ),
    )

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT source_policy_state FROM intake.intakes WHERE intake_id = %s", (ensure_uuid("IN-PROV"),))
    assert cur.fetchone()[0] == "AUTH_REQUIRED"

    cur.execute("SELECT raw_object_uri, content_sha256 FROM intake.source_snapshots WHERE intake_id = %s", (ensure_uuid("IN-PROV"),))
    row = cur.fetchone()
    assert row[0] == "gs://verified-bucket/snapshots/raw-100"
    assert row[1] == hashlib.sha256(json.dumps({"html": "custom snapshot text"}, sort_keys=True).encode()).hexdigest()

    cur.execute("SELECT artifact_uri, artifact_sha256, validation_status FROM intake.parser_releases WHERE semantic_version = '2.0'")
    pr_row = cur.fetchone()
    assert pr_row[0] == "gs://verified-parser-artifacts/v2.0.tar.gz"
    assert pr_row[1] == "b" * 64
    assert pr_row[2] == "VALIDATED"


def test_b5_probe_no_synthetic_intake_creation(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-REAL",
        **synthetic_history("IN-REAL"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-real.html",
        "submittedAt": "2026-07-14T06:10:00Z",
    }

    listing = Listing(listing_id="L-NO-INTAKE", source_listing_id="s-nointake", source_id="SRC-591")
    addr = AddressLocation(address_id="A-NO-INTAKE", normalized_address="台北市信義區松仁路 96 號 1F")
    cand = CandidateSite(candidate_site_id="CS-NO-INTAKE", listing_id="L-NO-INTAKE", address_id="A-NO-INTAKE")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    res = migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
    )

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT COUNT(*) FROM intake.intakes")
    intake_count = cur.fetchone()[0]

    assert intake_count == 1
    assert res["counts"]["quarantined"] >= 1
    assert res["counts"]["findings"] >= 1


def test_major_b_probe_complete_table_scoped_rollback(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-ROLLBACK",
        **synthetic_history("IN-ROLLBACK", {"html": "test snapshot"}),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-rb.html",
        "submittedAt": "2026-07-14T06:10:00Z",
        "rawSnapshot": {"html": "test snapshot"},
        "parsedFields": {"rent": 40000, "address": "台北市信義區松仁路 96 號 1F"},
        "matchResult": {"targetListingId": "L-ROLLBACK", "outcome": "NEW"},
    }
    listing = Listing(listing_id="L-ROLLBACK", source_listing_id="s-rb", source_id="SRC-591")
    addr = AddressLocation(address_id="A-ROLLBACK", normalized_address="台北市信義區松仁路 96 號 1F")
    cand = CandidateSite(candidate_site_id="CS-ROLLBACK", listing_id="L-ROLLBACK", address_id="A-ROLLBACK")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
    )

    # Perform scoped rollback
    migrator.rollback_migration(tenant_id=tenant_id)

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))

    state_tables = [
        "intake.intakes",
        "intake.source_snapshots",
        "intake.parser_runs",
        "intake.intake_stage_transitions",
        "identity.properties",
        "identity.source_identity_edges",
        "identity.match_cases",
        "identity.match_candidates",
        "identity.match_decisions",
        "expansion.listings",
        "expansion.listing_revisions",
        "expansion.listing_observations",
        "expansion.promotion_decisions",
        "expansion.candidate_sites",
        "workflow.outbox_events",
        "workflow.reconciliation_findings",
    ]

    for tbl in state_tables:
        cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE tenant_id = %s", (tenant_id,))
        count = cur.fetchone()[0]
        assert count == 0, f"Leftover records found in {tbl}: {count}"

    # Audit events are append-only/WORM and preserved per contract
    cur.execute("SELECT COUNT(*) FROM audit.audit_events WHERE tenant_id = %s", (tenant_id,))
    assert cur.fetchone()[0] >= 1


def test_backfill_dry_run_does_not_commit(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intakes = [
        {
            "id": "IN-2024",
            "tenantId": tenant_id,
            "sourceId": "SRC-591",
            "originalUrl": "https://591.com.tw/detail-2024.html",
            "canonicalUrl": "https://591.com.tw/detail-2024.html",
            **synthetic_history("IN-2024"),
            "stage": "READY",
        }
    ]

    res = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        dry_run=True,
    )

    assert res["counts"]["intakes_processed"] == 1
    assert res["dry_run"] is True

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT COUNT(*) FROM intake.intakes")
    assert cur.fetchone()[0] == 0


def test_backfill_resume_skips_existing(conn) -> None:
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intakes = [
        {
            "id": "IN-2024",
            "tenantId": tenant_id,
            "sourceId": "SRC-591",
            "originalUrl": "https://591.com.tw/detail-2024.html",
            "canonicalUrl": "https://591.com.tw/detail-2024.html",
            **synthetic_history("IN-2024"),
            "stage": "READY",
        }
    ]

    migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
    )

    res = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        resume=True,
    )

    assert res["counts"]["intakes_processed"] == 0
    assert res["counts"]["skipped_due_to_resume"] == 1


def test_blocker3_rollback_preserves_unrelated_tenant_data(conn) -> None:
    """Verify rollback safely preserves unrelated intakes and live audit events in the same tenant."""
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    # 1. Backfill migration data
    legacy_intake = {
        "id": "IN-MIG-1",
        **synthetic_history("IN-MIG-1"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-mig.html",
        "submittedAt": "2026-07-14T06:10:00Z",
    }
    migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
    )

    # 2. Insert unrelated live intake and unrelated live audit event
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute(
        """
        INSERT INTO intake.intakes (
            intake_id, tenant_id, heat_zone_id, submitter_subject_id,
            intake_method, original_url, canonical_url, canonical_url_sha256,
            source_id, source_policy_state, processing_state, resolved_listing_id,
            correlation_id, version, submitted_at, last_transition_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
        """,
        (
            ensure_uuid("IN-LIVE-001"),
            tenant_id,
            ensure_uuid("HZ-01"),
            ensure_uuid("user-1"),
            "URL",
            "https://591.com.tw/live.html",
            "https://591.com.tw/live.html",
            "sha-live",
            "SRC-591",
            "APPROVED_RETRIEVAL",
            "READY",
            None,
            ensure_uuid("corr-live"),
        ),
    )
    cur.execute(
        """
        INSERT INTO audit.audit_events (
            audit_event_id, tenant_id, sequence_no, event_type, actor_subject_id,
            action, resource_type, resource_id, decision_id, result, correlation_id,
            event_sha256, occurred_at, retained_until
        ) VALUES (%s, %s, 999, 'LIVE_EVENT', %s, 'LIVE_ACTION', 'Intake', %s, NULL, 'SUCCEEDED', %s, 'sha-audit', NOW(), NOW())
        """,
        (
            ensure_uuid("AUDIT-LIVE-001"),
            tenant_id,
            ensure_uuid("actor-live"),
            ensure_uuid("IN-LIVE-001"),
            ensure_uuid("corr-live"),
        ),
    )
    conn.commit()

    # 3. Roll back migration
    deleted_count = migrator.rollback_migration(tenant_id=tenant_id)
    assert deleted_count >= 1

    # 4. Verify unrelated intake and audit event survived
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT intake_id FROM intake.intakes WHERE tenant_id = %s", (tenant_id,))
    surviving_intakes = [r[0] for r in cur.fetchall()]
    assert len(surviving_intakes) == 1
    assert str(surviving_intakes[0]) == ensure_uuid("IN-LIVE-001")

    cur.execute("SELECT audit_event_id, event_type FROM audit.audit_events WHERE tenant_id = %s", (tenant_id,))
    surviving_audits = cur.fetchall()
    assert len(surviving_audits) == 1
    assert str(surviving_audits[0][0]) == ensure_uuid("AUDIT-LIVE-001")
    assert surviving_audits[0][1] == "LIVE_EVENT"


def test_blocker2_month_partitioning_no_fabricated_created_at_date(conn) -> None:
    """Candidate draft with no legacy intake/timestamp must trigger INVALID_SCOPE when filtering by month."""
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    listing = Listing(listing_id="L-NO-TS", source_listing_id="s-nots", source_id="SRC-591")
    addr = AddressLocation(address_id="A-NO-TS", normalized_address="台北市信義區松仁路 96 號 1F")
    cand = CandidateSite(candidate_site_id="CS-NO-TS", listing_id="L-NO-TS", address_id="A-NO-TS")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    res = migrator.backfill(
        legacy_intakes=[],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
        month="2026-07",
    )

    assert res["counts"]["listings_processed"] == 0
    assert res["counts"]["candidates_processed"] == 0
    assert res["counts"]["quarantined"] >= 1
    assert res["counts"]["findings"] >= 1


def test_cli_subprocess_against_pg_fixture(intake_blank_db, tmp_path) -> None:
    """CLI execution end-to-end via subprocess against PostgreSQL DSN."""
    import json
    import subprocess
    import sys

    admin_params = dict(intake_blank_db.server.admin_params)
    admin_params["dbname"] = intake_blank_db.dbname
    dsn = " ".join(f"{k}={v}" for k, v in admin_params.items())
    tenant_id = "00000000-0000-0000-0000-000000000001"
    input_file = tmp_path / "input.json"
    input_file.write_text(
        json.dumps(
            {
                "legacy_intakes": [
                    {
                        "id": "IN-CLI-1",
                        **synthetic_history("IN-CLI-1"),
                        "tenantId": tenant_id,
                        "sourceId": "SRC-591",
                        "originalUrl": "https://591.com.tw/detail-cli.html",
                        "submittedAt": "2026-07-14T06:10:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # 1. Backfill dry-run CLI
    proc_dry = subprocess.run(
        [
            sys.executable,
            "-m",
            "product_ops.migrations.assisted_listing_intake.migrate",
            "--action",
            "backfill",
            "--tenant-id",
            tenant_id,
            "--db-dsn",
            dsn,
            "--input-file",
            str(input_file),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert proc_dry.returncode == 0, f"CLI dry-run failed stderr: {proc_dry.stderr}"
    assert "dry_run" in proc_dry.stdout

    # 2. Partitioned backfill CLI with --month and --resume
    proc_bf = subprocess.run(
        [
            sys.executable,
            "-m",
            "product_ops.migrations.assisted_listing_intake.migrate",
            "--action",
            "backfill",
            "--tenant-id",
            tenant_id,
            "--source-id",
            "SRC-591",
            "--month",
            "2026-07",
            "--db-dsn",
            dsn,
            "--input-file",
            str(input_file),
            "--resume",
        ],
        capture_output=True,
        text=True,
    )
    assert proc_bf.returncode == 0, f"CLI backfill failed stderr: {proc_bf.stderr}"
    assert "intakes_processed" in proc_bf.stdout

    # 3. Verify CLI
    proc_ver = subprocess.run(
        [
            sys.executable,
            "-m",
            "product_ops.migrations.assisted_listing_intake.migrate",
            "--action",
            "verify",
            "--tenant-id",
            tenant_id,
            "--db-dsn",
            dsn,
        ],
        capture_output=True,
        text=True,
    )
    assert proc_ver.returncode == 0, f"CLI verify failed stderr: {proc_ver.stderr}"
    assert "shadow_comparison_success" in proc_ver.stdout

    # 4. Rollback CLI with --migration-ref
    proc_rb = subprocess.run(
        [
            sys.executable,
            "-m",
            "product_ops.migrations.assisted_listing_intake.migrate",
            "--action",
            "rollback",
            "--tenant-id",
            tenant_id,
            "--migration-ref",
            "ODP-INTAKE-MIGRATION-001",
            "--db-dsn",
            dsn,
        ],
        capture_output=True,
        text=True,
    )
    assert proc_rb.returncode == 0, f"CLI rollback failed stderr: {proc_rb.stderr}"
    assert "Scoped migration rollback executed" in proc_rb.stdout


def test_cli_fail_closed_without_database_target() -> None:
    """CLI must fail closed with exit code 1 if no database DSN or sqlite-path is specified."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if "DATABASE_URL" not in k and "ODAY_" not in k and "INTAKE_" not in k}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "product_ops.migrations.assisted_listing_intake.migrate",
            "--action",
            "backfill",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode != 0
    assert "Error: No database connection target provided" in proc.stderr


def test_blocker2_rollback_scoped_to_migration_ref(conn) -> None:
    """Verify rollback of MIG-REF-B does not destroy intakes or identity created by MIG-REF-A."""
    migrator_a = IntakeMigrator(conn, migration_ref="MIG-REF-A")
    migrator_a.apply_schema()
    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake_a = {
        "id": "IN-A-1",
        **synthetic_history("IN-A-1"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-a1.html",
        "submittedAt": "2026-07-14T06:10:00Z",
    }
    migrator_a.backfill(
        legacy_intakes=[legacy_intake_a],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
    )

    migrator_b = IntakeMigrator(conn, migration_ref="MIG-REF-B")
    legacy_intake_b = {
        "id": "IN-B-1",
        **synthetic_history("IN-B-1"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-b1.html",
        "submittedAt": "2026-07-14T06:10:00Z",
    }
    migrator_b.backfill(
        legacy_intakes=[legacy_intake_b],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
    )

    # Roll back only MIG-REF-B
    migrator_b.rollback_migration(migration_ref="MIG-REF-B", tenant_id=tenant_id)

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT intake_id FROM intake.intakes WHERE tenant_id = %s", (tenant_id,))
    surviving_intakes = [str(r[0]) for r in cur.fetchall()]

    assert len(surviving_intakes) == 1
    assert surviving_intakes[0] == ensure_uuid("IN-A-1")


def test_blocker3_rollback_preserves_live_outbox_and_orphan_properties(conn) -> None:
    """Verify rollback preserves pre-existing CandidateSitePromoted outbox events and orphan properties."""
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()
    tenant_id = "00000000-0000-0000-0000-000000000001"

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))

    # Insert pre-existing property (PROP-LIVE) with no listing
    prop_live_id = ensure_uuid("PROP-LIVE")
    cur.execute(
        """
        INSERT INTO identity.properties (
            property_id, tenant_id, normalized_address, address_fingerprint, status, version
        ) VALUES (%s, %s, '台北市信義區松仁路 1 號', 'sha-live-prop', 'ACTIVE', 1)
        """,
        (prop_live_id, tenant_id),
    )

    # Insert pre-existing runtime outbox event (OB-LIVE)
    ob_live_id = ensure_uuid("OB-LIVE")
    cur.execute(
        """
        INSERT INTO workflow.outbox_events (
            outbox_event_id, tenant_id, event_id, event_type, event_version,
            aggregate_type, aggregate_id, aggregate_version, partition_key,
            payload, correlation_id, occurred_at, retention_until
        ) VALUES (%s, %s, %s, 'CandidateSitePromoted', 1, 'CandidateSite', %s, 1, %s, '{}', %s, NOW(), NOW())
        """,
        (ob_live_id, tenant_id, ensure_uuid("evt-live"), ensure_uuid("CS-LIVE"), tenant_id, ensure_uuid("corr-live")),
    )
    conn.commit()

    # Backfill migration candidate
    listing = Listing(listing_id="L-MIG", source_listing_id="s-mig", source_id="SRC-591")
    addr = AddressLocation(address_id="A-MIG", normalized_address="台北市信義區松仁路 96 號 1F")
    cand = CandidateSite(candidate_site_id="CS-MIG", listing_id="L-MIG", address_id="A-MIG")
    draft = CandidateSiteDraft(listing=listing, address=addr, candidate_site=cand)

    legacy_intake = {
        "id": "IN-MIG",
        **synthetic_history("IN-MIG"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-mig.html",
        "submittedAt": "2026-07-14T06:10:00Z",
        "matchResult": {"targetListingId": "L-MIG", "outcome": "NEW"},
    }

    migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[listing],
        legacy_candidates=[draft],
        tenant_id=tenant_id,
    )

    # Roll back migration
    migrator.rollback_migration(tenant_id=tenant_id)

    # Assert live property and outbox event survived
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT property_id FROM identity.properties WHERE tenant_id = %s", (tenant_id,))
    surviving_props = [str(r[0]) for r in cur.fetchall()]
    assert len(surviving_props) == 1
    assert surviving_props[0] == prop_live_id

    cur.execute("SELECT outbox_event_id FROM workflow.outbox_events WHERE tenant_id = %s", (tenant_id,))
    surviving_outbox = [str(r[0]) for r in cur.fetchall()]
    assert len(surviving_outbox) == 1
    assert surviving_outbox[0] == ob_live_id


def test_blocker4_resume_then_verify_cutover_path(conn) -> None:
    """Verify interrupt -> resume -> verify cutover path yields shadow_comparison_success=True with 0 findings."""
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()
    tenant_id = "00000000-0000-0000-0000-000000000001"

    in1 = {
        "id": "IN-R1",
        **synthetic_history("IN-R1"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-r1.html",
        "submittedAt": "2026-07-14T06:10:00Z",
    }
    in2 = {
        "id": "IN-R2",
        **synthetic_history("IN-R2"),
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-r2.html",
        "submittedAt": "2026-07-14T06:10:00Z",
    }

    # Pass 1: backfill IN-R1
    migrator.backfill(
        legacy_intakes=[in1],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
    )

    # Pass 2: resume backfill with [IN-R1, IN-R2]
    res_resume = migrator.backfill(
        legacy_intakes=[in1, in2],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        resume=True,
    )
    assert res_resume["counts"]["skipped_due_to_resume"] == 1
    assert res_resume["counts"]["intakes_processed"] == 1

    # Fresh migrator instance verifies shadow comparison against persisted proof
    fresh_migrator = IntakeMigrator(conn)
    verify_res = fresh_migrator.verify_shadow_comparison(tenant_id)

    assert verify_res["intake_count"] == 2
    assert verify_res["blocking_findings"] == 0
    assert verify_res["shadow_comparison_success"] is True


def test_major_c_multi_partition_checksum_and_preexisting_tenant_rows(conn) -> None:
    """Verify multi-partition backfill verifies checksums for every partition correctly."""
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()
    tenant_id = "00000000-0000-0000-0000-000000000001"

    in_jan = {
        "id": "IN-JAN",
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-jan.html",
        **synthetic_history("IN-JAN"),
        "submittedAt": "2026-01-15T06:10:00Z",
    }
    in_feb = {
        "id": "IN-FEB",
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-feb.html",
        **synthetic_history("IN-FEB"),
        "submittedAt": "2026-02-15T06:10:00Z",
    }

    # Partition 1: Jan 2026
    migrator.backfill(
        legacy_intakes=[in_jan],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        month="2026-01",
    )

    # Partition 2: Feb 2026
    migrator.backfill(
        legacy_intakes=[in_feb],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        month="2026-02",
    )

    verify_res = migrator.verify_shadow_comparison(tenant_id)
    assert verify_res["intake_count"] == 2
    assert verify_res["blocking_findings"] == 0
    assert verify_res["shadow_comparison_success"] is True


def test_blocker1_multi_partition_listings_and_candidates(conn) -> None:
    """BLOCKER 1 regression: two-month partitioned cutover with listings AND candidates.

    Reviewer scenario (ai-activity-log.jsonl 2026-07-21T02:30:15Z):
    - Run backfill for month 2026-01 with 1 intake + 1 listing + 1 candidate.
    - Run backfill for month 2026-02 with 1 intake + 1 listing + 1 candidate.
    - Create a fresh migrator (simulates 'verify from a fresh migrator').
    - verify_shadow_comparison must return shadow_comparison_success=True with 0 blocking findings.

    Before the fix, verify_shadow_comparison built each partition's listing actuals from
    the whole-tenant listing set filtered only by source_id (no month). With two partitions
    (both source SRC-591), each partition saw all 2 listings, producing COUNT_MISMATCH
    (expected 1, got 2) x2 and CHECKSUM_MISMATCH x2 per entity type = 8 BLOCKING findings.
    """
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()
    tenant_id = "00000000-0000-0000-0000-000000000001"

    # January partition: intake IN-JAN, listing L-JAN, candidate CS-JAN
    in_jan = {
        "id": "IN-JAN",
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-jan.html",
        **synthetic_history("IN-JAN"),
        "submittedAt": "2026-01-15T06:10:00Z",
        "matchResult": {"outcome": "NEW", "confidence": 1.0, "targetListingId": "L-JAN"},
    }
    lst_jan = Listing(
        listing_id="L-JAN",
        source_listing_id="src-jan",
        source_id="SRC-591",
        listing_status="new",
        rent_amount=30000.0,
        snapshot_id="https://591.com.tw/detail-jan.html",
    )
    addr_jan = AddressLocation(
        address_id="ADDR-JAN",
        raw_address="台北市信義區松仁路 10 號 1F",
        normalized_address="台北市信義區松仁路 10 號 1F",
        latitude=25.0330,
        longitude=121.5654,
    )
    cand_jan = CandidateSite(
        candidate_site_id="CS-JAN",
        listing_id="L-JAN",
        address_id="ADDR-JAN",
        site_status="new",
    )
    draft_jan = CandidateSiteDraft(
        listing=lst_jan,
        address=addr_jan,
        candidate_site=cand_jan,
        heat_zone_id="HZ-JAN",
        status="CANDIDATE",
    )

    # February partition: intake IN-FEB, listing L-FEB, candidate CS-FEB
    in_feb = {
        "id": "IN-FEB",
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-feb.html",
        **synthetic_history("IN-FEB"),
        "submittedAt": "2026-02-15T06:10:00Z",
        "matchResult": {"outcome": "NEW", "confidence": 1.0, "targetListingId": "L-FEB"},
    }
    lst_feb = Listing(
        listing_id="L-FEB",
        source_listing_id="src-feb",
        source_id="SRC-591",
        listing_status="new",
        rent_amount=35000.0,
        snapshot_id="https://591.com.tw/detail-feb.html",
    )
    addr_feb = AddressLocation(
        address_id="ADDR-FEB",
        raw_address="台北市大安區新生南路一段 20 號 2F",
        normalized_address="台北市大安區新生南路一段 20 號 2F",
        latitude=25.0400,
        longitude=121.5300,
    )
    cand_feb = CandidateSite(
        candidate_site_id="CS-FEB",
        listing_id="L-FEB",
        address_id="ADDR-FEB",
        site_status="new",
    )
    draft_feb = CandidateSiteDraft(
        listing=lst_feb,
        address=addr_feb,
        candidate_site=cand_feb,
        heat_zone_id="HZ-FEB",
        status="CANDIDATE",
    )

    # Partition 1: backfill January (runbook 3.2 run #1)
    r1 = migrator.backfill(
        legacy_intakes=[in_jan],
        legacy_listings=[lst_jan],
        legacy_candidates=[draft_jan],
        tenant_id=tenant_id,
        month="2026-01",
    )
    assert r1["counts"]["intakes_processed"] == 1
    assert r1["counts"]["listings_processed"] == 1
    assert r1["counts"]["candidates_processed"] == 1

    # Partition 2: backfill February (runbook 3.2 run #2)
    r2 = migrator.backfill(
        legacy_intakes=[in_feb],
        legacy_listings=[lst_feb],
        legacy_candidates=[draft_feb],
        tenant_id=tenant_id,
        month="2026-02",
    )
    assert r2["counts"]["intakes_processed"] == 1
    assert r2["counts"]["listings_processed"] == 1
    assert r2["counts"]["candidates_processed"] == 1

    # Simulate 'fresh migrator' — new instance without in-memory state, reads proofs from DB
    fresh_migrator = IntakeMigrator(conn)

    verify_res = fresh_migrator.verify_shadow_comparison(tenant_id)

    # Must be 0 blocking findings and shadow_comparison_success=True
    assert verify_res["blocking_findings"] == 0, (
        f"Expected 0 blocking findings but got {verify_res['blocking_findings']}. "
        f"Full result: {verify_res}"
    )
    assert verify_res["shadow_comparison_success"] is True
    assert verify_res["intake_count"] == 2
    assert verify_res["listing_count"] == 2
    assert verify_res["candidate_count"] == 2


def migration_input(bridge=False):
    tenant = "00000000-0000-0000-0000-000000000001"
    row = {
        "id": "IN-INPUT", "tenantId": tenant, **synthetic_history("IN-INPUT"),
        "originalUrl": "https://591.com.tw/input?tracking=synthetic",
        "canonicalUrl": "https://591.com.tw/input", "sourceId": "SRC-591",
        "sourcePolicyState": "AUTH_REQUIRED", "intakeMethod": "MANUAL",
        "stage": "NEEDS_REVIEW", "submitter": "Synthetic display label (not an ID)",
        "parsedFields": {"rent": 12345}, "normalizedFields": {"rent": 12345}, "matchResult": {"outcome": "NEW", "confidence": 0.5},
        "heatZoneId": ensure_uuid("synthetic-zone"), "brandId": ensure_uuid("synthetic-brand"),
        "regionId": ensure_uuid("synthetic-region"), "assignedAreaId": ensure_uuid("synthetic-area"),
    }
    if not bridge:
        return row
    names = {
        "id": "intake_id", "stage": "state", "originalUrl": "original_url",
        "canonicalUrl": "canonical_url", "sourceId": "source_id", "submittedBy": "submitted_by",
        "submittedAt": "submitted_at", "intakeMethod": "intake_method", "version": "version",
        "sourcePolicyState": "policy_state",
    }
    return {
        **{snake: row[camel] for camel, snake in names.items()},
        "scope": {"tenant_id": tenant, "heat_zone_id": row["heatZoneId"],
                  "brand_id": row["brandId"], "region_id": row["regionId"], "assigned_area_id": row["assignedAreaId"]},
        "operator_presentation": row,
    }


def parser_provenance(**overrides):
    return {
        "source_id": "SRC-591", "package_name": "synthetic-parser", "input_schema_version": "synthetic-input-v2",
        "output_schema_version": "synthetic-output-v3", "test_corpus_version": "synthetic-corpus-v4",
        "semantic_version": "1.4", "artifact_uri": "gs://synthetic-parser/1.4.tar.gz",
        "artifact_sha256": "c" * 64, "validation_status": "VALIDATED", **overrides,
    }


def test_legacy_bridge_field_and_evidence_parity(conn):
    """Compare actual SQL fields/evidence, not only the existing ID proof."""
    tenant = migration_input()["tenantId"]
    snapshots = []
    for bridge in (False, True):
        migrator = IntakeMigrator(conn)
        result = migrator.backfill([migration_input(bridge)], [], [], tenant_id=tenant, parser_release=parser_provenance())
        assert result["counts"]["intakes_processed"] == 1
        assert result["counts"]["findings"] == 0
        cur = conn.cursor()
        cur.execute("SELECT submitter_subject_id, submitted_at, intake_method, processing_state, source_id, source_policy_state, original_url, canonical_url, version, heat_zone_id FROM intake.intakes")
        intake_row = cur.fetchone()
        assert str(intake_row[0]) == synthetic_history("IN-INPUT")["submittedBy"]
        assert intake_row[1].isoformat() == "2026-07-14T06:10:00+00:00"
        assert intake_row[2:9] == ("MANUAL", "NEEDS_REVIEW", "SRC-591", "AUTH_REQUIRED", "https://591.com.tw/input?tracking=synthetic", "https://591.com.tw/input", 1)
        assert str(intake_row[9]) == ensure_uuid("synthetic-zone")
        cur.execute("SELECT brand_id, region_id, assigned_area_id FROM intake.intakes")
        assert tuple(str(value) for value in cur.fetchone()) == tuple(ensure_uuid("synthetic-" + name) for name in ("brand", "region", "area"))
        cur.execute("SELECT raw_object_uri, content_sha256, byte_length, captured_at, observed_at, capture_method, media_type, retention_class, encryption_key_ref FROM intake.source_snapshots")
        snapshot_row = cur.fetchone()
        assert snapshot_row[:2] == ("odp-artifact://synthetic/IN-INPUT", synthetic_history("IN-INPUT")["rawSnapshotSha256"])
        assert snapshot_row[2] == len(json.dumps({"synthetic_intake": "IN-INPUT"}).encode())
        assert snapshot_row[3].isoformat() == "2026-07-14T06:11:00+00:00"
        assert snapshot_row[5:] == ("SERVER_RETRIEVAL", "application/json", "STANDARD", "kms://synthetic-key")
        cur.execute("SELECT object_generation, residency_mode FROM intake.source_snapshots")
        assert cur.fetchone() == (123, "TW_ONLY")
        cur.execute("SELECT input_schema_version, output_schema_version, test_corpus_version FROM intake.parser_releases")
        assert cur.fetchone() == ("synthetic-input-v2", "synthetic-output-v3", "synthetic-corpus-v4")
        cur.execute("SELECT status, parsed_payload, normalized_payload FROM intake.parser_runs")
        parser_row = cur.fetchone()
        assert parser_row == ("SUCCEEDED", {"rent": 12345}, {"rent": 12345})
        cur.execute("SELECT reason_text, resulting_version, service_principal, occurred_at FROM intake.intake_stage_transitions")
        transition = cur.fetchone()
        history = json.loads(transition[0])["source_history"]
        assert history["version"] == 7
        assert history["submittedAt"] == "2026-07-14T06:10:00Z"
        assert transition[1:3] == (1, "migration_worker")
        assert transition[3] > intake_row[1]
        cur.execute("SELECT status FROM identity.match_cases")
        assert cur.fetchone() == ("PENDING_REVIEW",)
        cur.execute("SELECT count(*) FROM identity.match_decisions")
        assert cur.fetchone()[0] == 0
        fresh = IntakeMigrator(conn).verify_shadow_comparison(tenant)
        assert fresh["blocking_findings"] == 0
        assert fresh["shadow_comparison_success"] is True
        assert fresh["proof_scope"] == "counts_and_ids_only_not_field_or_evidence_parity"
        snapshots.append((intake_row, snapshot_row, parser_row, history))
        migrator.rollback_migration(tenant_id=tenant)
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize("field,value,kind", [
    ("id", "different-id", "STATE_MAPPING_CONFLICT"),
    ("tenantId", "00000000-0000-0000-0000-000000000002", "INVALID_SCOPE"),
    ("stage", "READY", "STATE_MAPPING_CONFLICT"),
    ("submittedBy", "10000000-0000-0000-0000-000000000002", "STATE_MAPPING_CONFLICT"),
    ("submittedAt", "2026-08-01T00:00:00Z", "STATE_MAPPING_CONFLICT"),
    ("intakeMethod", "URL", "STATE_MAPPING_CONFLICT"),
    ("version", 9, "STATE_MAPPING_CONFLICT"),
    ("originalUrl", "https://591.com.tw/other", "STATE_MAPPING_CONFLICT"),
    ("canonicalUrl", "https://591.com.tw/other", "STATE_MAPPING_CONFLICT"),
    ("sourceId", "SRC-BROKER", "STATE_MAPPING_CONFLICT"),
    ("sourcePolicyState", "APPROVED_RETRIEVAL", "STATE_MAPPING_CONFLICT"),
])
def test_bridge_conflicting_authoritative_aliases_are_findings(conn, field, value, kind):
    row = migration_input(True)
    row["operator_presentation"][field] = value
    tenant = row["scope"]["tenant_id"]
    result = IntakeMigrator(conn).backfill([row], [], [], tenant_id=tenant)
    assert result["counts"]["intakes_quarantined"] == 1
    cur = conn.cursor()
    for table in ("intake.intakes", "intake.source_snapshots", "intake.parser_runs", "identity.match_decisions"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0
    cur.execute("SELECT finding_type FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
    assert cur.fetchall() == [(kind,)]
    assert IntakeMigrator(conn).verify_shadow_comparison(tenant)["shadow_comparison_success"] is False


@pytest.mark.parametrize("field,value", [
    ("submittedAt", None), ("submittedAt", "not-a-date"), ("submittedAt", "2026-07-14T06:10:00"),
    ("submittedBy", None), ("submittedBy", "Display Name"),
    ("intakeMethod", None), ("intakeMethod", "BATCH_ASSISTED_ENTRY"),
    ("stage", None), ("stage", "COMPLETED"), ("version", 0), ("version", "7"),
    ("tenantId", None), ("tenantId", "tenant-a"), ("sourceId", None), ("sourceId", "SRC-UNKNOWN"),
])
def test_missing_or_invalid_history_never_invents_facts(conn, field, value):
    row = migration_input()
    tenant = row["tenantId"]
    row[field] = value
    row["submitter"] = "Looks like a person"
    row["firstSeenAt"] = "2026-07-14T06:10:00Z"  # Not a submission timestamp.
    result = IntakeMigrator(conn).backfill([row], [], [], tenant_id=tenant)
    assert result["counts"]["intakes_received"] == 1
    assert result["counts"]["intakes_processed"] == 0
    assert result["counts"]["intakes_quarantined"] == 1
    cur = conn.cursor()
    for table in ("intake.intakes", "intake.intake_stage_transitions", "intake.source_snapshots", "intake.parser_runs"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
    assert cur.fetchone()[0] >= 1


@pytest.mark.parametrize("scope", [None, [], {"tenant_id": "wrong-tenant"}, {}, {"tenant_id": "00000000-0000-0000-0000-000000000001", "heat_zone_id": "bad-zone"}])
def test_bridge_invalid_scope_cannot_fall_back_to_presentation(conn, scope):
    row = migration_input(True)
    row["scope"] = scope
    tenant = row["operator_presentation"]["tenantId"]
    result = IntakeMigrator(conn).backfill([row], [], [], tenant_id=tenant)
    assert result["counts"]["intakes_quarantined"] == 1
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM intake.intakes")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT finding_type FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
    assert cur.fetchall() == [("INVALID_SCOPE",)]


@pytest.mark.parametrize("field,value,snapshots", [
    ("rawSnapshot", None, 0), ("rawSnapshot", {}, 0), ("rawSnapshot", "", 0),
    ("rawSnapshotSha256", "a" * 64, 0), ("rawObjectUri", None, 0),
    ("rawObjectUri", "unverified://placeholder/snapshot", 0),
    ("capturedAt", None, 0), ("observedAt", "not-a-date", 0),
    ("captureMethod", None, 0), ("encryptionKeyRef", None, 0),
    ("parsedFields", {}, 1), ("parsedFields", None, 1),
    ("parserVersion", None, 1), ("parserRunStatus", None, 1),
])
def test_display_defaults_and_missing_provenance_never_create_success(conn, field, value, snapshots):
    row = migration_input(True)
    row["operator_presentation"][field] = value
    tenant = row["scope"]["tenant_id"]
    result = IntakeMigrator(conn).backfill([row], [], [], tenant_id=tenant, parser_release=parser_provenance())
    assert result["counts"]["intakes_processed"] == 1
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM intake.source_snapshots")
    assert cur.fetchone()[0] == snapshots
    cur.execute("SELECT count(*) FROM intake.parser_runs")
    assert cur.fetchone()[0] == 0
    if field not in {"parsedFields"}:
        cur.execute("SELECT count(*) FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
        assert cur.fetchone()[0] >= 1
        assert IntakeMigrator(conn).verify_shadow_comparison(tenant)["shadow_comparison_success"] is False


@pytest.mark.parametrize("release", [
    parser_provenance(source_id="SRC-BROKER"), parser_provenance(validation_status="DRAFT"),
    parser_provenance(artifact_sha256="bad"), parser_provenance(artifact_uri="unverified://parser/missing"),
    parser_provenance(source_id=None), parser_provenance(semantic_version=None),
    parser_provenance(input_schema_version=None), parser_provenance(test_corpus_version=None),
])
def test_parser_provenance_must_be_valid_and_source_bound(conn, release):
    row = migration_input()
    IntakeMigrator(conn).backfill([row], [], [], tenant_id=row["tenantId"], parser_release=release)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM intake.source_snapshots")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM intake.parser_runs")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
    assert cur.fetchone()[0] >= 1


@pytest.mark.parametrize("overrides", [
    {},  # matching pre-existing release remains usable
    {"source_id": "SRC-BROKER"}, {"semantic_version": "2.0"},
    {"input_schema_version": None}, {"output_schema_version": None},
    {"test_corpus_version": None}, {"package_name": None},
    {"package_name": "different-package"}, {"input_schema_version": "different-input"},
    {"output_schema_version": "different-output"}, {"test_corpus_version": "different-corpus"},
    {"validation_status": "DRAFT"}, {"artifact_uri": "gs://other/parser.tar.gz"},
    {"artifact_sha256": "d" * 64}, {"parser_release_id": ensure_uuid("different-release")},
    {"parser_release_id": ensure_uuid("pre-existing-release")},
])
def test_parser_input_cannot_borrow_preexisting_release(conn, overrides):
    row = migration_input()
    tenant = row["tenantId"]
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()
    existing = parser_provenance(parser_release_id=ensure_uuid("pre-existing-release"))
    migrator.register_sources_and_parsers(parser_release=existing, tenant_id=tenant)
    conn.commit()

    # A new migrator must validate this call, not rely on registration or history
    # from the previous invocation. All cases start with the same VALIDATED row.
    result = IntakeMigrator(conn).backfill(
        [row], [], [], tenant_id=tenant, parser_release=parser_provenance(**overrides),
    )
    assert result["counts"]["intakes_processed"] == 1
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM intake.source_snapshots")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT source_id, package_name, semantic_version, input_schema_version, output_schema_version, artifact_uri, artifact_sha256, test_corpus_version, validation_status FROM intake.parser_releases WHERE parser_release_id = %s", (existing["parser_release_id"],))
    assert cur.fetchone() == tuple(existing[key] for key in (
        "source_id", "package_name", "semantic_version", "input_schema_version", "output_schema_version",
        "artifact_uri", "artifact_sha256", "test_corpus_version", "validation_status",
    ))
    accepted = not overrides or overrides == {"parser_release_id": existing["parser_release_id"]}
    cur.execute("SELECT parser_release_id, status FROM intake.parser_runs")
    runs = cur.fetchall()
    cur.execute("SELECT count(*) FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
    blocking = cur.fetchone()[0]
    if accepted:
        assert [(str(release_id), status) for release_id, status in runs] == [(existing["parser_release_id"], "SUCCEEDED")]
        assert blocking == result["counts"]["findings"] == 0
    else:
        assert runs == []
        assert blocking >= 1
    assert IntakeMigrator(conn).verify_shadow_comparison(tenant)["shadow_comparison_success"] is accepted


@pytest.mark.parametrize("bridge", [False, True])
@pytest.mark.parametrize("partition", ["tenant", "source"])
def test_malformed_excluded_history_never_enters_partition_findings(conn, bridge, partition):
    row = migration_input(bridge)
    tenant = migration_input()["tenantId"]
    if bridge:
        row["submitted_at"] = row["operator_presentation"]["submittedAt"] = None
        if partition == "tenant":
            row["scope"]["tenant_id"] = row["operator_presentation"]["tenantId"] = "00000000-0000-0000-0000-000000000002"
        else:
            row["source_id"] = row["operator_presentation"]["sourceId"] = "SRC-BROKER"
    else:
        row["submittedAt"] = None
        if partition == "tenant":
            row["tenantId"] = "00000000-0000-0000-0000-000000000002"
        else:
            row["sourceId"] = "SRC-BROKER"
    result = IntakeMigrator(conn).backfill(
        [row], [], [], tenant_id=tenant, source_id="SRC-591", parser_release=parser_provenance(),
    )
    counts = result["counts"]
    assert counts["intakes_received"] == counts["intakes_excluded"] == 1
    assert counts["intakes_processed"] == counts["intakes_quarantined"] == counts["quarantined"] == counts["findings"] == 0
    cur = conn.cursor()
    for table in ("intake.intakes", "intake.intake_stage_transitions", "intake.source_snapshots", "intake.parser_runs"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0
    cur.execute("SELECT tenant_id, severity, expected, actual FROM workflow.reconciliation_findings")
    findings = cur.fetchall()
    assert len(findings) == 1  # Only the aggregate count/ID proof, no excluded row history.
    saved_tenant, severity, expected, actual = findings[0]
    assert str(saved_tenant) == tenant
    assert severity == "INFO"
    assert expected == actual
    assert expected["intakes_received"] == expected["intakes_excluded"] == 1
    assert expected["intakes"] == expected["intakes_quarantined"] == 0
    serialized = json.dumps(expected)
    assert "IN-INPUT" not in serialized and "source_history" not in serialized
    assert synthetic_history("IN-INPUT")["submittedBy"] not in serialized
    assert "00000000-0000-0000-0000-000000000002" not in serialized
    assert IntakeMigrator(conn).verify_shadow_comparison(tenant)["shadow_comparison_success"] is True


@pytest.mark.parametrize("invalid_scope", ["missing", "conflicting"])
def test_excluded_source_does_not_hide_untrusted_tenant_scope(conn, invalid_scope):
    row = migration_input(True)
    tenant = row["scope"]["tenant_id"]
    row["source_id"] = row["operator_presentation"]["sourceId"] = "SRC-BROKER"
    if invalid_scope == "missing":
        row["scope"].pop("tenant_id")
    else:
        row["operator_presentation"]["tenantId"] = "00000000-0000-0000-0000-000000000002"
    result = IntakeMigrator(conn).backfill(
        [row], [], [], tenant_id=tenant, source_id="SRC-591", parser_release=parser_provenance(),
    )
    assert result["counts"]["intakes_excluded"] == result["counts"]["intakes_processed"] == 0
    assert result["counts"]["intakes_quarantined"] == 1
    cur = conn.cursor()
    cur.execute("SELECT finding_type FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
    assert cur.fetchall() == [("INVALID_SCOPE",)]
    cur.execute("SELECT count(*) FROM intake.intakes")
    assert cur.fetchone()[0] == 0
    assert IntakeMigrator(conn).verify_shadow_comparison(tenant)["shadow_comparison_success"] is False


def test_tenant_partition_documents_exclusion_and_dry_run_invalid_input(conn):
    row = migration_input()
    tenant = row["tenantId"]
    row["tenantId"] = "00000000-0000-0000-0000-000000000002"
    migrator = IntakeMigrator(conn)
    result = migrator.backfill([row], [], [], tenant_id=tenant)
    assert result["counts"]["intakes_received"] == result["counts"]["intakes_excluded"] == 1
    assert result["counts"]["intakes_processed"] == result["counts"]["intakes_quarantined"] == 0
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM intake.intakes")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT expected FROM workflow.reconciliation_findings WHERE severity='INFO'")
    assert cur.fetchone()[0]["intakes_excluded"] == 1
    row["tenantId"] = "not-a-tenant"
    result = migrator.backfill([row], [], [], tenant_id=tenant, dry_run=True)
    assert result["counts"]["intakes_quarantined"] == 1
    cur.execute("SELECT count(*) FROM workflow.reconciliation_findings WHERE severity='BLOCKING'")
    assert cur.fetchone()[0] == 0


# Inventory is an operational read of the same migration inputs, not an export
# or another persistence path. Every fixture below is an owned local database.
INVENTORY_TENANT = "00000000-0000-0000-0000-000000000001"
INVENTORY_OTHER = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def inventory_db(intake_blank_db):
    import uuid

    from shared.infrastructure.persistence.postgresql import PostgresEngine

    with intake_blank_db.connect() as connection:
        IntakeMigrator(connection).apply_schema()
    engine = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    role = "inventory_" + uuid.uuid4().hex
    try:
        with intake_blank_db.connect() as connection:
            connection.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
            connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
            connection.execute(f'REVOKE CREATE, TEMPORARY ON DATABASE "{intake_blank_db.dbname}" FROM PUBLIC')
            connection.execute(f'GRANT USAGE ON SCHEMA intake, odp_runtime TO "{role}"')
            connection.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA intake, odp_runtime TO "{role}"')
        yield intake_blank_db, engine, role
    finally:
        engine.close()
        with intake_blank_db.connect() as connection:
            assert connection.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = %s", (role,),
            ).fetchone()[0] == 0
            connection.execute(f'DROP OWNED BY "{role}"')
            connection.execute(f'DROP ROLE "{role}"')


def inventory_snapshot(database):
    """Hash all application rows and catalog definitions, including evidence."""
    with database.connect() as connection:
        schemas = ("intake", "identity", "expansion", "workflow", "audit", "odp_runtime")
        relations = connection.execute(
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname = ANY(%s) AND c.relkind='r' ORDER BY 1,2", (list(schemas),),
        ).fetchall()
        snapshots = []
        for schema, table in relations:
            rows = connection.execute(f'SELECT to_jsonb(t) FROM "{schema}"."{table}" t ORDER BY to_jsonb(t)::text').fetchall()
            snapshots.append((schema, table, rows))
        for catalog, namespace in (("pg_class", "relnamespace"), ("pg_type", "typnamespace")):
            snapshots.append(connection.execute(
                f"SELECT to_jsonb(c) FROM {catalog} c JOIN pg_namespace n ON n.oid=c.{namespace} "
                "WHERE n.nspname = ANY(%s) ORDER BY c.oid", (list(schemas),),
            ).fetchall())
        for catalog, relation in (("pg_attribute", "attrelid"), ("pg_constraint", "conrelid"),
                                  ("pg_policy", "polrelid"), ("pg_attrdef", "adrelid"), ("pg_trigger", "tgrelid")):
            snapshots.append(connection.execute(
                f"SELECT to_jsonb(a) FROM {catalog} a JOIN pg_class c ON c.oid=a.{relation} "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname = ANY(%s) "
                "ORDER BY to_jsonb(a)::text", (list(schemas),),
            ).fetchall())
    return hashlib.sha256(json.dumps(snapshots, default=str, sort_keys=True).encode()).hexdigest()


def run_inventory_cli(database, role, *selectors, tenant=INVENTORY_TENANT, expected_exit=0, libpq=False):
    import os
    import subprocess
    import sys

    if libpq:
        from psycopg.conninfo import make_conninfo

        dsn = make_conninfo(**{**database.server.admin_params, "dbname": database.dbname, "user": role})
    else:
        dsn = database.url().replace("postgres@", role + "@")
    env = {key: value for key, value in os.environ.items() if "DATABASE_URL" not in key}
    env["MLFLOW_DISABLE_AGENT_HINT"] = "1"  # Unrelated installed MLflow startup advertising.
    args = [sys.executable, "-m", "product_ops.migrations.assisted_listing_intake.migrate",
            "--action", "inventory", "--db-dsn", dsn, *selectors]
    if tenant is not None:
        args.extend(["--tenant-id", tenant])
    before = inventory_snapshot(database)
    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=45)
    assert proc.returncode == expected_exit, proc.stderr + proc.stdout
    assert proc.stderr == ""
    assert len(proc.stdout) < 16000
    for secret in (dsn, "tracking=synthetic", "Synthetic display label", "kms://", "591.com.tw", "synthetic-key"):
        assert secret not in proc.stdout
    assert inventory_snapshot(database) == before
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = %s", (role,)).fetchone()[0] == 0
    return json.loads(proc.stdout)


def save_inventory_document(engine, value, *, bridge=False, tenant=INVENTORY_TENANT, key=None):
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
    from shared.infrastructure.persistence.postgresql import PostgresDocumentStore

    store = PostgresDocumentStore(engine)
    intake_id = key or value.get("intake_id") or value["id"]
    if bridge:
        store.put("operator.assisted_intakes", f"{tenant}:{intake_id}",
                  DurableAssistedIntakeStore._wrap(tenant, intake_id, value))
    else:
        TenantScopedDocumentStore(store, tenant).put("operator.assisted_intakes", intake_id, value)


def seed_inventory_models(database, engine):
    """Real legacy/bridge storage and native SQL, with mirrored logical IDs."""
    legacy = migration_input()
    bridge = migration_input(True)
    # Mirror a legacy non-UUID through the migrator's original UUID mapping.
    bridge["intake_id"] = bridge["operator_presentation"]["id"] = ensure_uuid(legacy["id"])
    save_inventory_document(engine, legacy)
    save_inventory_document(engine, bridge, bridge=True)
    with database.connect() as connection:
        IntakeMigrator(connection).backfill([legacy], [], [], tenant_id=INVENTORY_TENANT, parser_release=parser_provenance())
        # Native-only, different source and month (not a mirrored receipt).
        row = {**legacy, "id": "native-other", "sourceId": "SRC-BROKER", "submittedAt": "2026-08-01T01:00:00Z"}
        IntakeMigrator(connection).backfill([row], [], [], tenant_id=INVENTORY_TENANT)
        # A real foreign-tenant row and both foreign document forms must not leak.
        row = {**legacy, "id": "foreign-native", "tenantId": INVENTORY_OTHER}
        IntakeMigrator(connection).backfill([row], [], [], tenant_id=INVENTORY_OTHER)
    foreign = {**legacy, "id": "foreign-document", "tenantId": INVENTORY_OTHER}
    save_inventory_document(engine, foreign, tenant=INVENTORY_OTHER)
    foreign_bridge = migration_input(True)
    foreign_bridge["scope"]["tenant_id"] = foreign_bridge["operator_presentation"]["tenantId"] = INVENTORY_OTHER
    save_inventory_document(engine, foreign_bridge, bridge=True, tenant=INVENTORY_OTHER)


def test_inventory_real_select_only_cli_overlap_and_partitions(inventory_db):
    database, engine, role = inventory_db
    seed_inventory_models(database, engine)
    import psycopg

    # No role-level read-only default: the implementation must set READ ONLY.
    with database.connect(user=role) as connection:
        assert connection.execute("SHOW default_transaction_read_only").fetchone()[0] == "off"
        assert connection.execute("SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone() == (False,) * 4
        assert connection.execute("SELECT has_database_privilege(current_database(), 'CREATE'), has_database_privilege(current_database(), 'TEMP')").fetchone() == (False, False)
        assert connection.execute("SELECT has_table_privilege('odp_runtime.durable_documents','SELECT'), has_table_privilege('intake.intakes','SELECT')").fetchone() == (True, True)
        for relation in ("odp_runtime.durable_documents", "intake.intakes"):
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                assert connection.execute("SELECT has_table_privilege(%s, %s)", (relation, privilege)).fetchone()[0] is False
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("DELETE FROM odp_runtime.durable_documents WHERE false")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE SCHEMA inventory_forbidden")
    result = run_inventory_cli(database, role)
    assert result["status"] == "complete"
    assert result["binding"]["tenant_id"] == INVENTORY_TENANT
    assert result["binding"]["read_only"] == "on"
    assert result["binding"]["isolation"] == "repeatable read"
    assert result["binding"]["snapshot"] and result["binding"]["captured_at"]
    assert result["proof_scope"] == "counts_and_ids_only_not_field_or_evidence_parity"
    assert result["partition_presence"] == "nonempty"
    assert {key: value["counts"]["selected"] for key, value in result["models"].items()} == {
        "legacy_documents": 1, "bridge_documents": 1, "native_rows": 2,
    }
    assert result["logical_ids"]["selected_union"] == 2  # Not four business receipts.
    assert set(result["logical_ids"]["overlap"].values()) == {1}
    partition = run_inventory_cli(database, role, "--source-id", "SRC-591", "--month", "2026-07", libpq=True)
    assert partition["logical_ids"]["selected_union"] == 1
    assert partition["models"]["native_rows"]["counts"] == {"observed": 2, "selected": 1, "excluded": 1, "unknown": 0}
    assert partition["binding"]["source_selector_sha256"] == hashlib.sha256(b"SRC-591").hexdigest()
    empty_partition = run_inventory_cli(database, role, "--source-id", "SRC-591", "--month", "2026-08")
    assert empty_partition["partition_presence"] == "empty"
    assert empty_partition["logical_ids"]["selected_union"] == 0
    foreign_partition = run_inventory_cli(database, role, tenant=INVENTORY_OTHER)
    assert foreign_partition["models"]["native_rows"]["counts"]["observed"] == 1
    assert foreign_partition["models"]["bridge_documents"]["counts"]["observed"] == 1


@pytest.mark.parametrize("submitted_at,utc_month,literal_month", [
    ("2026-07-01T00:30:00+08:00", "2026-06", "2026-07"),
    ("2026-06-30T23:30:00-07:00", "2026-07", "2026-06"),
])
@pytest.mark.parametrize("session_timezone", ["UTC", "Asia/Taipei"])
def test_inventory_offset_month_shared_with_backfill_and_verify(
    inventory_db, submitted_at, utc_month, literal_month, session_timezone,
):
    """Equivalent timestamptz projections must not invent conflicting history.

    Month selectors consistently mean UTC instant-month, never the serialization
    offset or the database session zone. Original source history remains intact.
    """
    from datetime import datetime

    database, engine, role = inventory_db
    legacy = migration_input()
    legacy["submittedAt"] = submitted_at
    bridge = migration_input(True)
    bridge["intake_id"] = bridge["operator_presentation"]["id"] = ensure_uuid(legacy["id"])
    bridge["submitted_at"] = bridge["operator_presentation"]["submittedAt"] = submitted_at
    save_inventory_document(engine, legacy)
    save_inventory_document(engine, bridge, bridge=True)
    with database.connect() as connection:
        connection.execute(f'ALTER ROLE "{role}" SET timezone = \'{session_timezone}\'')
        IntakeMigrator(connection).backfill(
            [legacy], [], [], tenant_id=INVENTORY_TENANT, parser_release=parser_provenance(),
        )
    result = run_inventory_cli(database, role)
    assert result["coverage"]["scope_history_conflicting_logical_ids"] == 0
    assert result["logical_ids"]["selected_union"] == 1
    partition = run_inventory_cli(database, role, "--source-id", "SRC-591", "--month", utc_month)
    assert partition["binding"]["month_basis"] == "UTC"
    assert all(model["counts"]["selected"] == 1 for model in partition["models"].values())
    excluded = run_inventory_cli(database, role, "--month", literal_month)
    assert excluded["partition_presence"] == "empty"
    assert all(model["counts"]["excluded"] == 1 for model in excluded["models"].values())
    # Exercise original write/verify owners only in this owned synthetic DB.
    # Verify both existing unpartitioned history and a new partitioned backfill.
    with database.connect() as connection:
        connection.execute(f"SET TIME ZONE '{session_timezone}'")
        assert IntakeMigrator(connection).verify_shadow_comparison(INVENTORY_TENANT)["blocking_findings"] == 0
        IntakeMigrator(connection).rollback_migration(tenant_id=INVENTORY_TENANT)
        written = IntakeMigrator(connection).backfill(
            [legacy], [], [], tenant_id=INVENTORY_TENANT, source_id="SRC-591", month=utc_month,
            parser_release=parser_provenance(),
        )
        assert written["counts"]["intakes_processed"] == 1
        assert written["counts"]["findings"] == 0
        # Native datetime projection changes, but the instant and source text do not.
        connection.execute("SET TIME ZONE 'America/Los_Angeles'")
        native_time = connection.execute("SELECT submitted_at FROM intake.intakes").fetchone()[0]
        assert native_time == datetime.fromisoformat(submitted_at)
        history = connection.execute("SELECT reason_text FROM intake.intake_stage_transitions").fetchone()[0]
        assert json.loads(history)["source_history"]["submittedAt"] == submitted_at
        fresh = IntakeMigrator(connection).verify_shadow_comparison(INVENTORY_TENANT)
        assert fresh["blocking_findings"] == 0
        assert fresh["shadow_comparison_success"] is True
        assert fresh["proof_scope"] == "counts_and_ids_only_not_field_or_evidence_parity"


def test_inventory_zero_missing_schema_and_denial_are_distinct(inventory_db):
    database, _, role = inventory_db
    result = run_inventory_cli(database, role)
    assert result["partition_presence"] == "empty"
    assert result["logical_ids"]["selected_union"] == 0
    assert all(value["counts"]["observed"] == 0 for value in result["models"].values())
    with database.connect() as connection:
        connection.execute(f'REVOKE SELECT ON intake.intakes FROM "{role}"')
    denied = run_inventory_cli(database, role, expected_exit=1)
    assert denied["partition_presence"] == "unknown"
    assert denied["models"]["native_rows"] == {"status": "unavailable", "counts": None, "findings": {"permission_denied": 1}}
    with database.connect() as connection:
        connection.execute("DROP SCHEMA intake CASCADE")
    absent = run_inventory_cli(database, role, expected_exit=1)
    assert absent["models"]["native_rows"]["counts"] is None
    assert absent["models"]["native_rows"]["findings"] == {"schema_absent": 1}
    assert absent["models"]["legacy_documents"]["counts"]["observed"] == 0
    with database.connect() as connection:
        connection.execute("DROP SCHEMA odp_runtime CASCADE")
    all_absent = run_inventory_cli(database, role, expected_exit=1)
    assert all_absent["partition_presence"] == "unknown"
    assert all(value["counts"] is None for value in all_absent["models"].values())


@pytest.mark.parametrize("field,value", [
    ("tenantId", None), ("tenantId", "bad-tenant"), ("tenantId", INVENTORY_OTHER),
    ("submittedAt", None), ("submittedAt", "not-historical"), ("submittedAt", "2026-07-01T00:00:00"),
    ("submittedBy", None), ("sourceId", None),
])
def test_inventory_unknown_scope_history_not_hidden_by_selectors(inventory_db, field, value):
    database, engine, role = inventory_db
    row = migration_input()
    row[field] = value
    # The legacy partition belongs to the selected tenant even when its payload
    # is missing/wrong. Neither a different source nor month can hide that fact.
    row["sourceId"] = value if field == "sourceId" else "SRC-BROKER"
    save_inventory_document(engine, row)
    result = run_inventory_cli(database, role, "--source-id", "SRC-591", "--month", "1999-01", expected_exit=1)
    assert result["models"]["legacy_documents"]["counts"] == {"observed": 1, "selected": 0, "excluded": 0, "unknown": 1}
    assert result["partition_presence"] == "unknown"
    assert result["logical_ids"]["selected_union"] is None
    assert result["models"]["legacy_documents"]["findings"]


@pytest.mark.parametrize("fault", ["scope_missing", "scope_conflict", "source_conflict", "time_conflict", "wrapper_tenant", "wrapper_key", "malformed_pickle", "malformed_shape"])
def test_inventory_bridge_corruption_is_visible(inventory_db, fault):
    database, engine, role = inventory_db
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.postgresql import PostgresDocumentStore

    row = migration_input(True)
    key = row["intake_id"]
    wrapped = DurableAssistedIntakeStore._wrap(INVENTORY_TENANT, key, row)
    if fault == "scope_missing":
        row["scope"] = {}
    elif fault == "scope_conflict":
        row["operator_presentation"]["tenantId"] = INVENTORY_OTHER
    elif fault == "source_conflict":
        row["source_id"] = "SRC-BROKER"
    elif fault == "time_conflict":
        row["submitted_at"] = "2026-08-01T00:00:00Z"
    elif fault == "wrapper_tenant":
        wrapped["_tenant_id"] = INVENTORY_OTHER
    elif fault == "wrapper_key":
        wrapped["_state_key"] = "wrong-key"
    elif fault == "malformed_shape":
        wrapped["value"] = ["not an Intake"]
    PostgresDocumentStore(engine).put("operator.assisted_intakes", f"{INVENTORY_TENANT}:{key}", wrapped)
    if fault == "malformed_pickle":
        engine.execute("UPDATE odp_runtime.durable_documents SET data = ?", (b"not pickle: secret credentials",))
    result = run_inventory_cli(database, role, "--source-id", "SRC-591", "--month", "2026-07", expected_exit=1)
    bridge = result["models"]["bridge_documents"]
    assert bridge["status"] == "findings"
    assert bridge["counts"] == {"observed": 1, "selected": 0, "excluded": 0, "unknown": 1}
    assert bridge["findings"]
    assert "secret credentials" not in json.dumps(result)
    assert result["partition_presence"] == "unknown"


def test_inventory_limits_unattributed_and_cross_model_history(inventory_db):
    database, engine, role = inventory_db
    seed_inventory_models(database, engine)
    limited = run_inventory_cli(database, role, "--inventory-limit", "1", expected_exit=1)
    assert limited["models"]["native_rows"]["counts"] is None
    assert limited["models"]["native_rows"]["findings"] == {"row_limit_exceeded": 1}
    row = migration_input(True)
    row["source_id"] = row["operator_presentation"]["sourceId"] = "SRC-BROKER"
    save_inventory_document(engine, row, bridge=True)
    conflict = run_inventory_cli(database, role, expected_exit=1)
    assert conflict["coverage"]["scope_history_conflicting_logical_ids"] == 1
    assert conflict["logical_ids"]["selected_union"] is None
    from shared.infrastructure.persistence.postgresql import PostgresDocumentStore

    PostgresDocumentStore(engine).put("operator.assisted_intakes", "unattributed", {"not": "tenant bound"})
    unbound = run_inventory_cli(database, role, expected_exit=1)
    assert unbound["coverage"]["unattributed_bridge_keys"] == 1


@pytest.mark.parametrize("tenant,selectors", [
    (None, []), ("bad-tenant-secret", []), (INVENTORY_TENANT, ["--month", "2026-13"]),
    (INVENTORY_TENANT, ["--source-id", ""]), (INVENTORY_TENANT, ["--inventory-limit", "0"]),
    (INVENTORY_TENANT, ["--dry-run"]), (INVENTORY_TENANT, ["--resume"]),
    (INVENTORY_TENANT, ["--input-file", "secret-input.json"]),
])
def test_inventory_requires_explicit_valid_context(inventory_db, tenant, selectors):
    database, _, role = inventory_db
    result = run_inventory_cli(database, role, *selectors, tenant=tenant, expected_exit=2)
    assert result == {"action": "inventory", "status": "error", "reason": "invalid_inventory_arguments"}


def test_inventory_same_process_cleanup_on_success_and_failure(inventory_db, monkeypatch):
    from product_ops.migrations.assisted_listing_intake.migrate import _inventory_cli

    database, engine, role = inventory_db
    dsn = database.url().replace("postgres@", role + "@")
    assert _inventory_cli(dsn, INVENTORY_TENANT, source_id=None, month=None, limit=10000)["status"] == "complete"
    # Validation and transaction failures must release the pool, observed here
    # before interpreter teardown could hide a leaked connection.
    with pytest.raises(ValueError):
        _inventory_cli(dsn, "invalid-tenant", source_id=None, month=None, limit=10000)
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    query_one = PostgresEngine.query_one

    def fail_binding(self, sql, params=()):
        if "AS snapshot" in sql:
            raise RuntimeError("synthetic transaction failure: secret DSN")
        return query_one(self, sql, params)

    with monkeypatch.context() as patch:
        patch.setattr(PostgresEngine, "query_one", fail_binding)
        with pytest.raises(RuntimeError, match="synthetic transaction failure"):
            _inventory_cli(dsn, INVENTORY_TENANT, source_id=None, month=None, limit=10000)
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = %s", (role,)).fetchone()[0] == 0
    # The existing caller-owned engine is unaffected by inventory cleanup.
    assert engine.query_one("SELECT 1 AS n")["n"] == 1


def test_inventory_one_consistent_snapshot_across_models(inventory_db, monkeypatch):
    from product_ops.migrations.assisted_listing_intake.migrate import _inventory_cli
    from shared.infrastructure.persistence.postgresql import PostgresDocumentStore

    database, engine, role = inventory_db
    seed_inventory_models(database, engine)
    original_get = PostgresDocumentStore.get
    changed = False

    def concurrent_change(self, collection, key):
        nonlocal changed
        value = original_get(self, collection, key)
        if not changed:
            changed = True
            with database.connect() as connection:
                connection.execute("UPDATE intake.intakes SET source_id = NULL WHERE tenant_id = %s", (INVENTORY_TENANT,))
        return value

    with monkeypatch.context() as patch:
        patch.setattr(PostgresDocumentStore, "get", concurrent_change)
        result = _inventory_cli(database.url().replace("postgres@", role + "@"), INVENTORY_TENANT,
                                source_id="SRC-591", month="2026-07", limit=10000)
    assert changed
    assert result["status"] == "complete"  # Native reads retain the pre-update snapshot.
    assert result["models"]["native_rows"]["counts"] == {"observed": 2, "selected": 1, "excluded": 1, "unknown": 0}
    # A fresh real CLI snapshot must expose missing native source facts, even
    # with a source/month selector. It cannot silently turn them into zero.
    current = run_inventory_cli(database, role, "--source-id", "SRC-591", "--month", "2026-07", expected_exit=1)
    assert current["models"]["native_rows"]["counts"] == {"observed": 2, "selected": 0, "excluded": 0, "unknown": 2}
    assert current["models"]["native_rows"]["findings"] == {"MISSING_EVIDENCE:sourceId": 2}
