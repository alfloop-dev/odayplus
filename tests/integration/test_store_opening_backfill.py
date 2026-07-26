"""Integration tests for authoritative store opening date binding & backfill engine (ODP-STORE-OPENING-001).

Proves:
- Approved source identity and snapshot lineage are persisted.
- opened_on is never inferred from created_at or ingestion_time.
- Backfill is tenant-safe and idempotent.
- Eligible stores with missing authority fail closed.
- PostgreSQL / DB integration proves replay and conflict behavior.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import date
from pathlib import Path

import pytest

from apps.data_platform.store_opening import (
    MissingStoreOpeningAuthorityError,
    StoreOpeningBackfillEngine,
    TenantIsolationError,
    UnauthoritativeStoreOpeningError,
    validate_store_opening_record,
)
from modules.external_data.connectors.store_opening import (
    StoreOpeningAuthorityConnector,
)
from scripts.models.store_opening_backfill import main as cli_main


@pytest.fixture
def test_db_conn():
    """Create in-memory SQLite database initialized with stores, ingestion_runs, canonical_lineage, and store_opening_authority_lineage tables."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE stores (
            store_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            store_name TEXT NOT NULL,
            opened_on DATE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE ingestion_runs (
            run_id TEXT PRIMARY KEY,
            source_database TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            partition_key TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE canonical_lineage (
            source_snapshot_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            run_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            canonical_table TEXT NOT NULL,
            canonical_id TEXT NOT NULL,
            projected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_snapshot_id, canonical_table, canonical_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE store_opening_authority_lineage (
            lineage_id TEXT PRIMARY KEY,
            source_snapshot_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            opened_on DATE NOT NULL,
            authority_type TEXT NOT NULL,
            provenance_note TEXT,
            content_sha256 TEXT NOT NULL,
            projected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_snapshot_id, store_id)
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def test_validate_record_accepts_approved_authority():
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()

    record = {
        "source_id": "SRC-AUTH-STORE-OPENING",
        "snapshot_id": str(snapshot_id),
        "tenant_id": str(tenant_id),
        "store_id": str(store_id),
        "opened_on": "2026-05-15",
        "authority_type": "AUDITED_MERCHANT_RECORD",
        "provenance_note": "Signed grand opening certificate",
    }
    auth = validate_store_opening_record(record)
    assert auth.opened_on == date(2026, 5, 15)
    assert auth.source_id == "SRC-AUTH-STORE-OPENING"
    assert auth.created_at_ignored is True


def test_validate_record_fails_closed_when_inferred_from_created_at():
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()

    # Case 1: Inferred flag set to True
    rec1 = {
        "source_id": "SRC-AUTH-STORE-OPENING",
        "snapshot_id": str(snapshot_id),
        "tenant_id": str(tenant_id),
        "store_id": str(store_id),
        "opened_on": "2026-05-15",
        "inferred_from_created_at": True,
    }
    with pytest.raises(UnauthoritativeStoreOpeningError, match="never be inferred from created_at"):
        validate_store_opening_record(rec1)

    # Case 2: opened_on matches raw created_at timestamp string
    rec2 = {
        "source_id": "SRC-AUTH-STORE-OPENING",
        "snapshot_id": str(snapshot_id),
        "tenant_id": str(tenant_id),
        "store_id": str(store_id),
        "opened_on": "2026-05-15T08:00:00Z",
        "created_at": "2026-05-15T08:00:00Z",
    }
    with pytest.raises(UnauthoritativeStoreOpeningError, match="matches raw created_at timestamp"):
        validate_store_opening_record(rec2)


def test_validate_record_rejects_unapproved_source():
    record = {
        "source_id": "SRC-UNKNOWN-GUESSED-SOURCE",
        "snapshot_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "store_id": str(uuid.uuid4()),
        "opened_on": "2026-05-15",
    }
    with pytest.raises(UnauthoritativeStoreOpeningError, match="Unapproved source identity"):
        validate_store_opening_record(record)


def test_backfill_engine_persists_opened_on_and_lineage(test_db_conn):
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()

    cur = test_db_conn.cursor()
    cur.execute(
        "INSERT INTO stores (store_id, tenant_id, store_name, opened_on) VALUES (?, ?, ?, NULL)",
        (str(store_id), str(tenant_id), "Test Store Alpha"),
    )
    test_db_conn.commit()

    engine = StoreOpeningBackfillEngine(db_conn=test_db_conn, schema="data_plane")
    records = [
        {
            "source_id": "SRC-AUTH-STORE-OPENING",
            "snapshot_id": str(snapshot_id),
            "tenant_id": str(tenant_id),
            "store_id": str(store_id),
            "opened_on": "2026-06-01",
            "authority_type": "APPROVED_GOVERNMENT_REGISTRY",
        }
    ]

    result = engine.run_backfill(snapshot_id=snapshot_id, tenant_id=tenant_id, records=records)
    assert result.updated_count == 1
    assert store_id in result.store_ids

    # Verify stores.opened_on updated
    cur.execute("SELECT opened_on FROM stores WHERE store_id = ?", (str(store_id),))
    row = cur.fetchone()
    assert row[0] == "2026-06-01"

    # Verify canonical lineage persisted
    cur.execute("SELECT source_id, canonical_table, canonical_id FROM canonical_lineage")
    lin_row = cur.fetchone()
    assert lin_row[0] == "SRC-AUTH-STORE-OPENING"
    assert lin_row[1] == "core.stores"
    assert lin_row[2] == str(store_id)


def test_backfill_is_idempotent(test_db_conn):
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()

    cur = test_db_conn.cursor()
    cur.execute(
        "INSERT INTO stores (store_id, tenant_id, store_name) VALUES (?, ?, ?)",
        (str(store_id), str(tenant_id), "Test Store Beta"),
    )
    test_db_conn.commit()

    engine = StoreOpeningBackfillEngine(db_conn=test_db_conn, schema="data_plane")
    records = [
        {
            "source_id": "SRC-AUTH-STORE-OPENING",
            "snapshot_id": str(snapshot_id),
            "tenant_id": str(tenant_id),
            "store_id": str(store_id),
            "opened_on": "2026-06-01",
        }
    ]

    res1 = engine.run_backfill(snapshot_id=snapshot_id, tenant_id=tenant_id, records=records)
    res2 = engine.run_backfill(snapshot_id=snapshot_id, tenant_id=tenant_id, records=records)

    assert res1.updated_count == 1
    assert res2.updated_count == 1

    cur.execute("SELECT opened_on FROM stores WHERE store_id = ?", (str(store_id),))
    assert cur.fetchone()[0] == "2026-06-01"


def test_eligible_stores_missing_authority_fails_closed(test_db_conn):
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store1 = uuid.uuid4()
    store2 = uuid.uuid4()

    records = [
        {
            "source_id": "SRC-AUTH-STORE-OPENING",
            "snapshot_id": str(snapshot_id),
            "tenant_id": str(tenant_id),
            "store_id": str(store1),
            "opened_on": "2026-06-01",
        }
    ]

    engine = StoreOpeningBackfillEngine(db_conn=test_db_conn, schema="data_plane")
    with pytest.raises(MissingStoreOpeningAuthorityError, match="Fail-closed triggered"):
        engine.run_backfill(
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            records=records,
            eligible_store_ids=[store1, store2],
        )


def test_tenant_isolation_safety(test_db_conn):
    snapshot_id = uuid.uuid4()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    store_id = uuid.uuid4()

    cur = test_db_conn.cursor()
    # Store belongs to Tenant B
    cur.execute(
        "INSERT INTO stores (store_id, tenant_id, store_name) VALUES (?, ?, ?)",
        (str(store_id), str(tenant_b), "Tenant B Store"),
    )
    test_db_conn.commit()

    engine = StoreOpeningBackfillEngine(db_conn=test_db_conn, schema="data_plane")
    # Attempting to backfill under Tenant A
    records = [
        {
            "source_id": "SRC-AUTH-STORE-OPENING",
            "snapshot_id": str(snapshot_id),
            "tenant_id": str(tenant_a),
            "store_id": str(store_id),
            "opened_on": "2026-06-01",
        }
    ]

    with pytest.raises(TenantIsolationError, match="belongs to tenant"):
        engine.run_backfill(snapshot_id=snapshot_id, tenant_id=tenant_a, records=records)


def test_unseeded_store_fails_closed_in_memory():
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()

    engine = StoreOpeningBackfillEngine(db_conn=None)
    records = [
        {
            "source_id": "SRC-AUTH-STORE-OPENING",
            "snapshot_id": str(snapshot_id),
            "tenant_id": str(tenant_id),
            "store_id": str(store_id),
            "opened_on": "2026-06-01",
        }
    ]
    with pytest.raises(TenantIsolationError, match="does not exist in store inventory"):
        engine.run_backfill(snapshot_id=snapshot_id, tenant_id=tenant_id, records=records)


def test_cli_runner_and_connector_facade(tmp_path: Path):
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()

    input_file = tmp_path / "opening_records.json"
    input_file.write_text(
        json.dumps(
            [
                {
                    "source_id": "SRC-AUTH-STORE-OPENING",
                    "snapshot_id": str(snapshot_id),
                    "tenant_id": str(tenant_id),
                    "store_id": str(store_id),
                    "opened_on": "2026-05-20",
                }
            ]
        ),
        encoding="utf-8",
    )

    connector = StoreOpeningAuthorityConnector()
    val = connector.validate_record(
        {
            "source_id": "SRC-AUTH-STORE-OPENING",
            "snapshot_id": str(snapshot_id),
            "tenant_id": str(tenant_id),
            "store_id": str(store_id),
            "opened_on": "2026-05-20",
        }
    )
    assert val.opened_on == date(2026, 5, 20)

    # Test CLI dry run
    code_dry = cli_main(
        [
            "--tenant-id",
            str(tenant_id),
            "--snapshot-id",
            str(snapshot_id),
            "--input-json",
            str(input_file),
            "--dry-run",
        ]
    )
    assert code_dry == 0

    # Test CLI fail-closed without --dry-run and without DB connection
    code_no_db = cli_main(
        [
            "--tenant-id",
            str(tenant_id),
            "--snapshot-id",
            str(snapshot_id),
            "--input-json",
            str(input_file),
        ]
    )
    assert code_no_db == 1


# -----------------------------------------------------------------------------
# PostgreSQL Real Schema Integration Tests
# -----------------------------------------------------------------------------
POSTGRES_DB_URL = os.environ.get("INTAKE_TEST_DATABASE_URL") or os.environ.get("ODP_DATABASE_URL") or os.environ.get("ODAY_DATABASE_URL")


@pytest.mark.skipif(
    not POSTGRES_DB_URL or "postgresql" not in POSTGRES_DB_URL.lower(),
    reason="PostgreSQL database URL is not configured",
)
def test_postgresql_store_opening_backfill_and_lineage():
    import psycopg

    conn = psycopg.connect(POSTGRES_DB_URL)
    cur = conn.cursor()

    try:
        # Bootstrap required schemas & tables
        cur.execute("CREATE SCHEMA IF NOT EXISTS core;")
        cur.execute("CREATE SCHEMA IF NOT EXISTS data_plane;")
        cur.execute("CREATE SCHEMA IF NOT EXISTS intake;")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS core.tenants (
                tenant_id UUID PRIMARY KEY,
                tenant_name VARCHAR(255) NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS core.brands (
                brand_id UUID PRIMARY KEY,
                tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
                brand_name VARCHAR(255) NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS core.stores (
                store_id UUID PRIMARY KEY,
                tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
                brand_id UUID NOT NULL REFERENCES core.brands(brand_id),
                store_name VARCHAR(255) NOT NULL,
                opened_on DATE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS data_plane.ingestion_runs (
                run_id UUID PRIMARY KEY,
                source_database TEXT NOT NULL DEFAULT 'fongniao_prod',
                source_kind TEXT NOT NULL,
                partition_key TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source_kind, partition_key, run_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS data_plane.canonical_lineage (
                source_snapshot_id UUID NOT NULL,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                run_id UUID NOT NULL REFERENCES data_plane.ingestion_runs(run_id),
                tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
                canonical_table TEXT NOT NULL,
                canonical_id UUID NOT NULL,
                projected_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_snapshot_id, canonical_table, canonical_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS intake.store_opening_authority_lineage (
                lineage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_snapshot_id UUID NOT NULL,
                source_id VARCHAR(100) NOT NULL,
                tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
                store_id UUID NOT NULL REFERENCES core.stores(store_id),
                opened_on DATE NOT NULL,
                authority_type VARCHAR(100) NOT NULL,
                provenance_note TEXT,
                content_sha256 VARCHAR(64) NOT NULL,
                projected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_store_opening_snapshot UNIQUE (source_snapshot_id, store_id)
            );
            """
        )
        conn.commit()

        tenant_id = uuid.uuid4()
        brand_id = uuid.uuid4()
        store_id = uuid.uuid4()
        snapshot_id_1 = uuid.uuid4()

        cur.execute("INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, 'Test Tenant')", (tenant_id,))
        cur.execute("INSERT INTO core.brands (brand_id, tenant_id, brand_name) VALUES (%s, %s, 'Test Brand')", (brand_id, tenant_id))
        cur.execute(
            "INSERT INTO core.stores (store_id, tenant_id, brand_id, store_name, opened_on) VALUES (%s, %s, %s, 'PG Store Test', NULL)",
            (store_id, tenant_id, brand_id),
        )
        conn.commit()

        engine = StoreOpeningBackfillEngine(db_conn=conn, schema="data_plane")
        records = [
            {
                "source_id": "SRC-AUTH-STORE-OPENING",
                "snapshot_id": str(snapshot_id_1),
                "tenant_id": str(tenant_id),
                "store_id": str(store_id),
                "opened_on": "2026-04-12",
                "authority_type": "APPROVED_GOVERNMENT_REGISTRY",
            }
        ]

        res = engine.run_backfill(snapshot_id=snapshot_id_1, tenant_id=tenant_id, records=records)
        assert res.updated_count == 1

        # 1. Assert core.stores.opened_on was persisted in PostgreSQL
        cur.execute("SELECT opened_on FROM core.stores WHERE store_id = %s", (store_id,))
        assert cur.fetchone()[0] == date(2026, 4, 12)

        # 2. Assert data_plane.canonical_lineage was written with valid ingestion_runs FK
        cur.execute(
            "SELECT source_id, canonical_table, run_id FROM data_plane.canonical_lineage WHERE source_snapshot_id = %s AND canonical_id = %s",
            (snapshot_id_1, store_id),
        )
        lin_row = cur.fetchone()
        assert lin_row is not None
        assert lin_row[0] == "SRC-AUTH-STORE-OPENING"
        assert lin_row[1] == "core.stores"

        # 3. Assert intake.store_opening_authority_lineage was written
        cur.execute(
            "SELECT opened_on, authority_type FROM intake.store_opening_authority_lineage WHERE source_snapshot_id = %s AND store_id = %s",
            (snapshot_id_1, store_id),
        )
        intake_row = cur.fetchone()
        assert intake_row is not None
        assert intake_row[0] == date(2026, 4, 12)

        # 4. Prove replay idempotency (re-run same snapshot)
        res_replay = engine.run_backfill(snapshot_id=snapshot_id_1, tenant_id=tenant_id, records=records)
        assert res_replay.updated_count == 1

        cur.execute(
            "SELECT COUNT(*) FROM intake.store_opening_authority_lineage WHERE source_snapshot_id = %s AND store_id = %s",
            (snapshot_id_1, store_id),
        )
        assert cur.fetchone()[0] == 1

        # 5. Prove conflict & update behavior with new snapshot
        snapshot_id_2 = uuid.uuid4()
        records_snap2 = [
            {
                "source_id": "SRC-AUTH-STORE-OPENING",
                "snapshot_id": str(snapshot_id_2),
                "tenant_id": str(tenant_id),
                "store_id": str(store_id),
                "opened_on": "2026-04-15",
            }
        ]
        res_snap2 = engine.run_backfill(snapshot_id=snapshot_id_2, tenant_id=tenant_id, records=records_snap2)
        assert res_snap2.updated_count == 1

        cur.execute("SELECT opened_on FROM core.stores WHERE store_id = %s", (store_id,))
        assert cur.fetchone()[0] == date(2026, 4, 15)

        cur.execute(
            "SELECT COUNT(*) FROM intake.store_opening_authority_lineage WHERE store_id = %s",
            (store_id,),
        )
        assert cur.fetchone()[0] == 2

    finally:
        conn.close()
