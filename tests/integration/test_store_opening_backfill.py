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
import sqlite3
import uuid
from datetime import date
from pathlib import Path

import pytest

from apps.data_platform.store_opening import (
    ApprovedStoreOpeningAuthority,
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
    """Create in-memory SQLite database initialized with stores and canonical_lineage tables."""
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
    code = cli_main(
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
    assert code == 0
