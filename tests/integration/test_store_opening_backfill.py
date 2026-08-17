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
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from product_ops.modeling.store_opening_backfill import main as cli_main


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
    cur.execute(
        "SELECT source_id, opened_on FROM store_opening_authority_lineage "
        "WHERE source_snapshot_id = ? AND store_id = ?",
        (str(snapshot_id), str(store_id)),
    )
    assert cur.fetchone() == ("SRC-AUTH-STORE-OPENING", "2026-06-01")


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
    cur.execute("SELECT COUNT(*) FROM ingestion_runs")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT COUNT(*) FROM canonical_lineage")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT COUNT(*) FROM store_opening_authority_lineage")
    assert cur.fetchone()[0] == 1


def test_same_snapshot_conflict_fails_closed_and_rolls_back(test_db_conn):
    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()
    cur = test_db_conn.cursor()
    cur.execute(
        "INSERT INTO stores (store_id, tenant_id, store_name) VALUES (?, ?, ?)",
        (str(store_id), str(tenant_id), "Immutable Authority Store"),
    )
    test_db_conn.commit()
    engine = StoreOpeningBackfillEngine(db_conn=test_db_conn)
    base = {
        "source_id": "SRC-AUTH-STORE-OPENING",
        "snapshot_id": str(snapshot_id),
        "tenant_id": str(tenant_id),
        "store_id": str(store_id),
        "opened_on": "2026-06-01",
    }
    engine.run_backfill(snapshot_id=snapshot_id, tenant_id=tenant_id, records=[base])

    conflicting = {**base, "opened_on": "2026-06-02"}
    with pytest.raises(
        UnauthoritativeStoreOpeningError,
        match="Conflicting authoritative opened_on",
    ):
        engine.run_backfill(
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            records=[conflicting],
        )

    cur.execute("SELECT opened_on FROM stores WHERE store_id = ?", (str(store_id),))
    assert cur.fetchone()[0] == "2026-06-01"
    cur.execute("SELECT COUNT(*) FROM ingestion_runs")
    assert cur.fetchone()[0] == 1


def test_database_dry_run_requires_store_to_exist(test_db_conn):
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    with pytest.raises(TenantIsolationError, match="does not exist"):
        StoreOpeningBackfillEngine(db_conn=test_db_conn).run_backfill(
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            records=[
                {
                    "source_id": "SRC-AUTH-STORE-OPENING",
                    "snapshot_id": str(snapshot_id),
                    "tenant_id": str(tenant_id),
                    "store_id": str(store_id),
                    "opened_on": "2026-06-01",
                }
            ],
            dry_run=True,
        )


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


def test_database_inventory_derives_missing_eligible_stores(test_db_conn):
    tenant_id = uuid.uuid4()
    store_id = uuid.uuid4()
    test_db_conn.execute(
        "INSERT INTO stores (store_id, tenant_id, store_name) VALUES (?, ?, ?)",
        (str(store_id), str(tenant_id), "Missing Authority Store"),
    )
    test_db_conn.commit()

    with pytest.raises(MissingStoreOpeningAuthorityError, match=str(store_id)):
        StoreOpeningBackfillEngine(db_conn=test_db_conn).run_backfill(
            snapshot_id=uuid.uuid4(),
            tenant_id=tenant_id,
            records=[],
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


def test_cli_runner_and_connector_facade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for env_name in (
        "ODAY_DATABASE_URL",
        "ODP_DATABASE_URL",
        "INTAKE_TEST_DATABASE_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
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
    assert code_dry == 1

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
@pytest.mark.requires_live_env
def test_postgresql_store_opening_backfill_and_lineage(intake_blank_db):
    """Apply production DDL on disposable PG16 and prove authority/replay/conflict."""
    migration_root = Path("infra/db/migrations")
    with intake_blank_db.connect(autocommit=True) as conn:
        canonical_ddl = (
            migration_root / "000002_data_domain_canonical_entities.sql"
        ).read_text()
        # pgserver bundles PostgreSQL 16 but not contrib/PostGIS. Preserve the
        # production DDL apart from equivalent test-only UUID defaults and
            # inert geometry storage; 000010 itself is always applied verbatim.
        canonical_ddl = canonical_ddl.replace(
            'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";',
            "",
        ).replace(
            'CREATE EXTENSION IF NOT EXISTS "postgis";',
            "",
        )
        canonical_ddl = canonical_ddl.replace(
            "uuid_generate_v4()",
            "gen_random_uuid()",
        )
        canonical_ddl = canonical_ddl.replace(
            "GEOMETRY(Point, 4326)",
            "TEXT",
        ).replace(
            "GEOMETRY(Polygon, 4326)",
            "TEXT",
        )
        canonical_ddl = canonical_ddl.replace(
            "CREATE INDEX IF NOT EXISTS idx_address_locations_geom "
            "ON core.address_locations USING GIST(geom);",
            "",
        ).replace(
            "CREATE INDEX IF NOT EXISTS idx_h3_cells_geom "
            "ON geo.h3_cells USING GIST(geom);",
            "",
        )
        conn.execute(canonical_ddl)
        intake_ddl = (
            migration_root / "assisted_listing_intake" / "001_baseline.sql"
        ).read_text()
        conn.execute("CREATE SCHEMA IF NOT EXISTS intake")
        for table_name in ("source_registry", "intakes", "source_snapshots"):
            marker = f"CREATE TABLE IF NOT EXISTS intake.{table_name} ("
            start = intake_ddl.index(marker)
            end = intake_ddl.index("\n);", start) + 3
            conn.execute(intake_ddl[start:end])
        conn.execute(
                (migration_root / "000010_store_opening_authority_lineage.sql").read_text()
            )

        cur = conn.cursor()
        tenant_id = uuid.uuid4()
        brand_id = uuid.uuid4()
        store_id = uuid.uuid4()
        snapshot_id_1 = uuid.uuid4()
        intake_id = uuid.uuid4()
        policy_owner = uuid.uuid4()
        source_id = "SRC-AUTH-STORE-OPENING"

        cur.execute(
            "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, 'Test Tenant')",
            (tenant_id,),
        )
        cur.execute(
            "INSERT INTO core.brands "
            "(brand_id, tenant_id, brand_code, brand_name) "
            "VALUES (%s, %s, 'test-brand', 'Test Brand')",
            (brand_id, tenant_id),
        )
        cur.execute(
            "INSERT INTO core.stores (store_id, tenant_id, brand_id, store_name, opened_on) VALUES (%s, %s, %s, 'PG Store Test', NULL)",
            (store_id, tenant_id, brand_id),
        )
        cur.execute(
            """
            INSERT INTO intake.source_registry (
                source_id, display_name, canonicalization_rule_version,
                retrieval_mode, legal_approval_ref, policy_owner_subject_id,
                kill_switch, production_enabled
            ) VALUES (%s, 'Opening Authority', 'v1', 'APPROVED_RETRIEVAL',
                      'LEGAL-OPEN-001', %s, FALSE, TRUE)
            """,
            (source_id, policy_owner),
        )
        cur.execute(
            """
            INSERT INTO intake.intakes (
                intake_id, tenant_id, submitter_subject_id, intake_method,
                source_id, source_policy_state, processing_state, correlation_id
            ) VALUES (%s, %s, %s, 'APPROVED_FEED', %s,
                      'APPROVED_RETRIEVAL', 'READY', %s)
            """,
            (intake_id, tenant_id, policy_owner, source_id, uuid.uuid4()),
        )
        cur.execute(
            """
            INSERT INTO intake.source_snapshots (
                source_snapshot_id, tenant_id, intake_id, source_id,
                raw_object_uri, content_sha256, media_type, byte_length,
                captured_at, observed_at, capture_method, retention_class,
                encryption_key_ref
            ) VALUES (%s, %s, %s, %s, 'gs://authority/snapshot-1',
                      %s, 'application/json', 1, now(), now(),
                      'APPROVED_FEED', 'BUSINESS_5Y', 'kms://test')
            """,
            (snapshot_id_1, tenant_id, intake_id, source_id, "a" * 64),
        )

        engine = StoreOpeningBackfillEngine(db_conn=conn, schema="data_plane")
        records = [
            {
                "source_id": source_id,
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

        # 5. A later snapshot cannot silently rewrite the immutable opening date.
        snapshot_id_2 = uuid.uuid4()
        cur.execute(
            """
            INSERT INTO intake.source_snapshots (
                source_snapshot_id, tenant_id, intake_id, source_id,
                raw_object_uri, content_sha256, media_type, byte_length,
                captured_at, observed_at, capture_method, retention_class,
                encryption_key_ref
            ) VALUES (%s, %s, %s, %s, 'gs://authority/snapshot-2',
                      %s, 'application/json', 1, now(), now(),
                      'APPROVED_FEED', 'BUSINESS_5Y', 'kms://test')
            """,
            (snapshot_id_2, tenant_id, intake_id, source_id, "b" * 64),
        )
        records_snap2 = [
            {
                "source_id": source_id,
                "snapshot_id": str(snapshot_id_2),
                "tenant_id": str(tenant_id),
                "store_id": str(store_id),
                "opened_on": "2026-04-15",
            }
        ]
        with pytest.raises(
            UnauthoritativeStoreOpeningError,
            match="Conflicting authoritative opened_on",
        ):
            engine.run_backfill(
                snapshot_id=snapshot_id_2,
                tenant_id=tenant_id,
                records=records_snap2,
            )

        cur.execute("SELECT opened_on FROM core.stores WHERE store_id = %s", (store_id,))
        assert cur.fetchone()[0] == date(2026, 4, 12)

        cur.execute(
            "SELECT COUNT(*) FROM intake.store_opening_authority_lineage WHERE store_id = %s",
            (store_id,),
        )
        assert cur.fetchone()[0] == 1

        # 6. Two real concurrent transactions cannot persist conflicting
        # canonical dates or split the canonical and authority lineage.
        concurrent_store_id = uuid.uuid4()
        concurrent_snapshots = (uuid.uuid4(), uuid.uuid4())
        concurrent_dates = ("2026-05-01", "2026-05-02")
        cur.execute(
            "INSERT INTO core.stores "
            "(store_id, tenant_id, brand_id, store_name, opened_on) "
            "VALUES (%s, %s, %s, 'Concurrent PG Store', NULL)",
            (concurrent_store_id, tenant_id, brand_id),
        )
        for index, concurrent_snapshot_id in enumerate(concurrent_snapshots):
            cur.execute(
                """
                INSERT INTO intake.source_snapshots (
                    source_snapshot_id, tenant_id, intake_id, source_id,
                    raw_object_uri, content_sha256, media_type, byte_length,
                    captured_at, observed_at, capture_method, retention_class,
                    encryption_key_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, 'application/json', 1,
                          now(), now(), 'APPROVED_FEED', 'BUSINESS_5Y',
                          'kms://test')
                """,
                (
                    concurrent_snapshot_id,
                    tenant_id,
                    intake_id,
                    source_id,
                    f"gs://authority/concurrent-{index}",
                    str(index + 1) * 64,
                ),
            )

        start = threading.Barrier(2)

        def run_concurrent_writer(writer_index):
            record = {
                "source_id": source_id,
                "snapshot_id": str(concurrent_snapshots[writer_index]),
                "tenant_id": str(tenant_id),
                "store_id": str(concurrent_store_id),
                "opened_on": concurrent_dates[writer_index],
            }
            with intake_blank_db.connect(autocommit=False) as writer_conn:
                writer_engine = StoreOpeningBackfillEngine(
                    db_conn=writer_conn,
                    schema="data_plane",
                )
                start.wait(timeout=10)
                try:
                    writer_engine.run_backfill(
                        snapshot_id=concurrent_snapshots[writer_index],
                        tenant_id=tenant_id,
                        records=[record],
                    )
                except UnauthoritativeStoreOpeningError:
                    return ("rejected", writer_index)
                return ("persisted", writer_index)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(run_concurrent_writer, (0, 1)))

        assert sorted(outcome for outcome, _ in outcomes) == [
            "persisted",
            "rejected",
        ]
        winner_index = next(
            writer_index
            for outcome, writer_index in outcomes
            if outcome == "persisted"
        )
        winner_snapshot = concurrent_snapshots[winner_index]
        winner_date = date.fromisoformat(concurrent_dates[winner_index])

        cur.execute(
            "SELECT opened_on FROM core.stores WHERE store_id = %s",
            (concurrent_store_id,),
        )
        assert cur.fetchone()[0] == winner_date
        cur.execute(
            """
            SELECT source_snapshot_id, content_sha256
            FROM data_plane.canonical_lineage
            WHERE canonical_table = 'core.stores' AND canonical_id = %s
            """,
            (concurrent_store_id,),
        )
        canonical_rows = cur.fetchall()
        cur.execute(
            """
            SELECT source_snapshot_id, opened_on, content_sha256
            FROM intake.store_opening_authority_lineage
            WHERE store_id = %s
            """,
            (concurrent_store_id,),
        )
        authority_rows = cur.fetchall()
        assert len(canonical_rows) == 1
        assert len(authority_rows) == 1
        assert canonical_rows[0][0] == winner_snapshot
        assert authority_rows[0][0] == winner_snapshot
        assert authority_rows[0][1] == winner_date
        assert canonical_rows[0][1] == authority_rows[0][2]
