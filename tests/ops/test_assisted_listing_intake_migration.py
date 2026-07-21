from __future__ import annotations

import pytest

from modules.listing.domain.models import CandidateSiteDraft
from scripts.migrations.assisted_listing_intake.migrate import IntakeMigrator, ensure_uuid
from shared.domain.models import AddressLocation, CandidateSite, Listing

# Mark all tests as requiring a live PostgreSQL 16 server
pytestmark = pytest.mark.requires_live_env


def test_migration_schema_upgrade_and_rollback(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
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


def test_backfill_happy_path(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
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
            "rawObjectUri": "odp-artifact://snapshots/snap-2024",
            "rawSnapshotSha256": "a" * 64,
            "sourcePolicyState": "APPROVED_RETRIEVAL",
            "stage": "READY",
            "heatZoneId": "HZ-01",
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
        parser_release={
            "semantic_version": "1.4",
            "artifact_uri": "gs://parser-artifacts/v1.4.tar.gz",
            "artifact_sha256": "c" * 64,
        },
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


def test_b1_property_identity_preservation_and_geocodes(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
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


def test_b1_probe_null_coordinates_and_no_fabricated_identities(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
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


def test_b2_month_and_source_partition_filtering(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-JULY",
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


def test_b3_shadow_comparison_count_and_checksum_proof(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-1",
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


def test_b3_probe_shadow_proof_persistence_on_fresh_migrator(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator1 = IntakeMigrator(conn)
    migrator1.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-FRESH",
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


def test_b4_snapshot_provenance_and_policy_preservation(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-PROV",
        "tenantId": tenant_id,
        "sourceId": "SRC-591",
        "originalUrl": "https://591.com.tw/detail-prov.html",
        "rawObjectUri": "gs://verified-bucket/snapshots/raw-100",
        "rawSnapshotSha256": "a" * 64,
        "sourcePolicyState": "AUTH_REQUIRED",
        "rawSnapshot": {"html": "custom snapshot text"},
    }

    migrator.backfill(
        legacy_intakes=[legacy_intake],
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        parser_release={
            "semantic_version": "2.0",
            "artifact_uri": "gs://verified-parser-artifacts/v2.0.tar.gz",
            "artifact_sha256": "b" * 64,
            "validation_status": "VALIDATED",
        },
    )

    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT source_policy_state FROM intake.intakes WHERE intake_id = %s", (ensure_uuid("IN-PROV"),))
    assert cur.fetchone()[0] == "AUTH_REQUIRED"

    cur.execute("SELECT raw_object_uri, content_sha256 FROM intake.source_snapshots WHERE intake_id = %s", (ensure_uuid("IN-PROV"),))
    row = cur.fetchone()
    assert row[0] == "gs://verified-bucket/snapshots/raw-100"
    assert row[1] == "a" * 64

    cur.execute("SELECT artifact_uri, artifact_sha256, validation_status FROM intake.parser_releases WHERE semantic_version = '2.0'")
    pr_row = cur.fetchone()
    assert pr_row[0] == "gs://verified-parser-artifacts/v2.0.tar.gz"
    assert pr_row[1] == "b" * 64
    assert pr_row[2] == "VALIDATED"


def test_b5_probe_no_synthetic_intake_creation(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-REAL",
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


def test_major_b_probe_complete_table_scoped_rollback(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"

    legacy_intake = {
        "id": "IN-ROLLBACK",
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

    tables = [
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
        "audit.audit_events",
        "workflow.reconciliation_findings",
    ]

    for tbl in tables:
        cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE tenant_id = %s", (tenant_id,))
        count = cur.fetchone()[0]
        assert count == 0, f"Leftover records found in {tbl}: {count}"


def test_backfill_dry_run_does_not_commit(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
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


def test_backfill_resume_skips_existing(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
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
