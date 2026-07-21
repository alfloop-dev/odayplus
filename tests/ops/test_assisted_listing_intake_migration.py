from __future__ import annotations

import pytest

from modules.listing.domain.models import CandidateSiteDraft
from scripts.migrations.assisted_listing_intake.migrate import IntakeMigrator
from shared.domain.models import AddressLocation, CandidateSite, Listing

# Mark all tests as requiring a live PostgreSQL 16 server
pytestmark = pytest.mark.requires_live_env


def test_migration_schema_upgrade_and_rollback(intake_blank_db) -> None:
    # Use live database connection from the fixture
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
    
    # Setup legacy source inputs
    legacy_intakes = [
        {
            "id": "IN-2024",
            "tenantId": tenant_id,
            "sourceId": "SRC-591",
            "originalUrl": "https://591.com.tw/detail-2024.html",
            "canonicalUrl": "https://591.com.tw/detail-2024.html",
            "stage": "READY",
            "heatZoneId": "HZ-01",
            "correlationId": "corr-2024",
            "submittedAt": "2026-07-14T06:10:00Z",
            "rawSnapshot": {"html": "dummy raw data"},
            "parsedFields": {"rent": 58000, "address": "台北市信義區松仁路 96 號 1F"},
            "matchResult": {
                "outcome": "NEW",
                "confidence": 1.0,
            }
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

    # Run backfill
    res = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=legacy_listings,
        legacy_candidates=legacy_candidates,
        tenant_id=tenant_id,
    )

    assert res["counts"]["intakes_processed"] == 1
    assert res["counts"]["listings_processed"] == 1
    assert res["counts"]["candidates_processed"] == 1
    assert res["counts"]["findings"] == 0

    # Query target tables to verify data was correctly written
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
    assert snaps[0][0].startswith("gs://taiwan-snapshots/")

    cur.execute("SELECT decision_type, status FROM expansion.promotion_decisions")
    proms = cur.fetchall()
    assert len(proms) == 1
    assert proms[0][0] == "LEGACY_RECONCILED"
    assert proms[0][1] == "COMPLETED"

    # Verify shadow comparison metrics
    verify_res = migrator.verify_shadow_comparison(tenant_id)
    assert verify_res["intake_count"] == 1
    assert verify_res["listing_count"] == 1
    assert verify_res["candidate_count"] == 1
    assert verify_res["open_findings"] == 0
    assert verify_res["shadow_comparison_success"] is True


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

    # Verify database is empty after dry run
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

    # First run (commits)
    migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
    )

    # Second run (resume enabled)
    res = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_id,
        resume=True,
    )

    assert res["counts"]["intakes_processed"] == 0
    assert res["counts"]["skipped_due_to_resume"] == 1


def test_backfill_partition_filtering(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_a = "00000000-0000-0000-0000-00000000000a"
    tenant_b = "00000000-0000-0000-0000-00000000000b"
    
    legacy_intakes = [
        {
            "id": "IN-A",
            "tenantId": tenant_a,
            "sourceId": "SRC-591",
            "submittedAt": "2026-07-10T00:00:00Z",
        },
        {
            "id": "IN-B",
            "tenantId": tenant_b,
            "sourceId": "SRC-BROKER",
            "submittedAt": "2026-08-10T00:00:00Z",
        }
    ]

    # Filter by tenant_a
    res_tenant = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[],
        legacy_candidates=[],
        tenant_id=tenant_a,
    )
    assert res_tenant["counts"]["intakes_processed"] == 1

    # Filter by month August 2026
    res_month = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[],
        legacy_candidates=[],
        month="2026-08",
    )
    assert res_month["counts"]["intakes_processed"] == 1


def test_reconciliation_findings_and_duplicate_candidates(intake_blank_db) -> None:
    conn = intake_blank_db.connect()
    migrator = IntakeMigrator(conn)
    migrator.apply_schema()

    tenant_id = "00000000-0000-0000-0000-000000000001"
    
    # 1. Intake with missing URL (triggers MISSING_EVIDENCE finding)
    legacy_intakes = [
        {
            "id": "IN-BAD",
            "tenantId": tenant_id,
            "sourceId": "SRC-591",
            "originalUrl": None,
            "canonicalUrl": None,
            "stage": "READY",
        }
    ]

    # 2. Duplicate candidates for same property (triggers DUPLICATE_CANDIDATE finding)
    listing = Listing(
        listing_id="L-DUP",
        source_listing_id="s591-dup",
        source_id="SRC-591",
        listing_status="new",
        address_id="ADDR-DUP",
        rent_amount=50000.0,
        area_ping=20.0,
    )
    address = AddressLocation(
        address_id="ADDR-DUP",
        raw_address="台北市大安區和平東路二段",
        normalized_address="台北市大安區和平東路二段",
    )
    
    c1 = CandidateSite(candidate_site_id="CS-1", listing_id="L-DUP", address_id="ADDR-DUP")
    c2 = CandidateSite(candidate_site_id="CS-2", listing_id="L-DUP", address_id="ADDR-DUP")
    c3 = CandidateSite(candidate_site_id="CS-3", listing_id="L-MISSING", address_id="ADDR-MISSING")

    listing_missing = Listing(
        listing_id="L-MISSING",
        source_listing_id="s591-missing",
        source_id="SRC-591",
        address_id="ADDR-MISSING",
    )

    cd1 = CandidateSiteDraft(listing=listing, address=address, candidate_site=c1, status="CANDIDATE")
    cd2 = CandidateSiteDraft(listing=listing, address=address, candidate_site=c2, status="CANDIDATE")
    cd3 = CandidateSiteDraft(listing=listing_missing, address=address, candidate_site=c3, status="CANDIDATE")

    res = migrator.backfill(
        legacy_intakes=legacy_intakes,
        legacy_listings=[listing],
        # Feed cd1, cd2, and cd3 (cd3 triggers BLOCKING finding due to missing listing revision)
        legacy_candidates=[cd1, cd2, cd3],
        tenant_id=tenant_id,
    )

    assert res["counts"]["findings"] >= 3
    assert res["counts"]["quarantined"] >= 1

    # Verify findings are in database
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT finding_type, severity, status FROM workflow.reconciliation_findings")
    findings = cur.fetchall()
    
    types = {f[0] for f in findings}
    assert "MISSING_EVIDENCE" in types
    assert "DUPLICATE_CANDIDATE" in types
    assert "STATE_MAPPING_CONFLICT" in types

    verify_res = migrator.verify_shadow_comparison(tenant_id)
    assert verify_res["open_findings"] >= 3
    assert verify_res["blocking_findings"] >= 1
    assert verify_res["shadow_comparison_success"] is False
