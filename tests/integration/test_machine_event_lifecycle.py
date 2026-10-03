"""PostgreSQL real persistence tests for machine_status_events record lifecycle.

ODP-CDC-MACHINE-EVENT-LIFECYCLE-001
Requirement: ODP-FR-INT-001 / H07 Decision 4 (Soft delete + audit tombstone in parallel)

Settles with real PostgreSQL persistence (via intake_blank_db fixture):
1. Migration 000027 adds `record_status`, `created_at`, `updated_at` to `core.machine_status_events`
   without altering `000001_baseline_canonical_schema.sql` or regressing pre-cutover rows.
2. Pre-existing rows retain all device data and receive `record_status = 'active'`.
3. Migration rollback and re-apply idempotency.
4. Active -> soft-retired lifecycle transition via CDC projector and batch ingestion.
5. Soft-delete statement is tenant-scoped and version-guarded: cross-tenant or out-of-order
   deletes cannot regress or mutate unowned rows.
6. Stale replay guard: an older batch replay or duplicate cannot resurrect a soft-retired row.
7. Newer event reactivation: a genuinely newer event (version > tombstone) can update or reactivate.
8. Idempotent delete replay: duplicate deletes converge on `REPLAYED` without side-effects.
9. Alembic revision 0021 upgrade and downgrade execution.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from apps.data_platform.cdc import (
    CdcChangeEnvelope,
    CdcOperation,
    RedactionReport,
    ScopedCdcProjector,
    cdc_policy,
    plan_change_application,
)
from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import SourceEnvelope, SourceKind
from apps.data_platform.identifiers import (
    brand_id_for_merchant,
    machine_id_for_device,
    store_id_for_place,
    tenant_id_for_merchant,
)
from apps.data_platform.store import PsycopgCanonicalStore

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SQL = REPO_ROOT / "infra/db/migrations/000027_machine_status_events_record_lifecycle.sql"

MERCHANT_ID = "m-lifecycle-001"
TENANT_ID = tenant_id_for_merchant(MERCHANT_ID)
BRAND_ID = brand_id_for_merchant(MERCHANT_ID)
PLACE_ID = "p-lifecycle-001"
STORE_ID = store_id_for_place(PLACE_ID)
DEVICE_ID = "d-lifecycle-001"
MACHINE_ID = machine_id_for_device(DEVICE_ID)

MERCHANT_ID_B = "m-lifecycle-002"
TENANT_ID_B = tenant_id_for_merchant(MERCHANT_ID_B)
BRAND_ID_B = brand_id_for_merchant(MERCHANT_ID_B)
PLACE_ID_B = "p-lifecycle-002"
STORE_ID_B = store_id_for_place(PLACE_ID_B)
DEVICE_ID_B = "d-lifecycle-002"
MACHINE_ID_B = machine_id_for_device(DEVICE_ID_B)

RUN_ID = str(uuid4())
CONTROL_SCHEMA = "data_plane"


def _sha256(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


STATUS_MAPPING_CONTENT = """{
  "version": "lifecycle-test-v1",
  "approved_by": "test-suite",
  "approved_at": "2026-09-01T00:00:00Z",
  "mappings": {
    "device_connection": {
      "online": "online",
      "available": "available",
      "offline": "offline",
      "1": "online",
      "0": "offline"
    }
  }
}"""


BASELINE_PRE_CUTOVER_SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS {CONTROL_SCHEMA};

CREATE TABLE IF NOT EXISTS core.tenants (
    tenant_id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS core.brands (
    brand_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    brand_code VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS core.stores (
    store_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    brand_id UUID NOT NULL REFERENCES core.brands(brand_id),
    source_store_id VARCHAR(255) NOT NULL,
    store_code VARCHAR(100) NOT NULL,
    store_name VARCHAR(255) NOT NULL,
    store_status VARCHAR(50) NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS core.machines (
    machine_id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    source_machine_id VARCHAR(255) NOT NULL,
    machine_serial_no VARCHAR(255),
    equipment_brand_id VARCHAR(100),
    machine_family VARCHAR(50) NOT NULL DEFAULT 'washer',
    machine_type VARCHAR(100),
    capacity_kg NUMERIC(5, 2),
    capacity_band VARCHAR(50) DEFAULT 'medium',
    installed_on DATE,
    removed_on DATE,
    machine_status VARCHAR(50) NOT NULL DEFAULT 'active',
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    effective_to TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.transactions (
    transaction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_transaction_id VARCHAR(255),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID REFERENCES core.machines(machine_id),
    member_id VARCHAR(255),
    event_time TIMESTAMP WITH TIME ZONE NOT NULL,
    observation_time TIMESTAMP WITH TIME ZONE NOT NULL,
    payment_time TIMESTAMP WITH TIME ZONE,
    gross_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    net_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    currency VARCHAR(10) NOT NULL DEFAULT 'TWD',
    payment_method VARCHAR(50) NOT NULL DEFAULT 'cash',
    transaction_status VARCHAR(50) NOT NULL DEFAULT 'succeeded',
    refund_of_transaction_id UUID REFERENCES core.transactions(transaction_id),
    price_schedule_id VARCHAR(255),
    promotion_id VARCHAR(255),
    source_system VARCHAR(100) NOT NULL,
    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Baseline core.machine_status_events WITHOUT record_status or created_at/updated_at
CREATE TABLE IF NOT EXISTS core.machine_status_events (
    status_event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    event_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status_type VARCHAR(100) NOT NULL DEFAULT 'online',
    severity VARCHAR(50) NOT NULL DEFAULT 'info',
    error_code VARCHAR(100),
    resolved_time TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS idx_machine_status_machine_time ON core.machine_status_events(machine_id, event_time);
"""


def _seed_hierarchy(conn: Any) -> None:
    # Tenant A
    conn.execute(
        """
        INSERT INTO core.tenants (tenant_id, name, status)
        VALUES (%s, 'Lifecycle Merchant Tenant A', 'active')
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        (TENANT_ID,),
    )
    conn.execute(
        """
        INSERT INTO core.brands (brand_id, tenant_id, brand_code, name, status)
        VALUES (%s, %s, %s, 'Lifecycle Brand A', 'active')
        ON CONFLICT (brand_id) DO NOTHING
        """,
        (BRAND_ID, TENANT_ID, f"fongniao_{MERCHANT_ID}"),
    )
    conn.execute(
        """
        INSERT INTO core.stores (store_id, tenant_id, brand_id, source_store_id, store_code, store_name, store_status)
        VALUES (%s, %s, %s, %s, 'STORE_01', 'Lifecycle Store 1', 'open')
        ON CONFLICT (store_id) DO NOTHING
        """,
        (STORE_ID, TENANT_ID, BRAND_ID, PLACE_ID),
    )
    conn.execute(
        """
        INSERT INTO core.machines (machine_id, store_id, source_machine_id, machine_serial_no, machine_status)
        VALUES (%s, %s, %s, 'SN-001', 'active')
        ON CONFLICT (machine_id) DO NOTHING
        """,
        (MACHINE_ID, STORE_ID, DEVICE_ID),
    )

    # Tenant B
    conn.execute(
        """
        INSERT INTO core.tenants (tenant_id, name, status)
        VALUES (%s, 'Lifecycle Merchant Tenant B', 'active')
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        (TENANT_ID_B,),
    )
    conn.execute(
        """
        INSERT INTO core.brands (brand_id, tenant_id, brand_code, name, status)
        VALUES (%s, %s, %s, 'Lifecycle Brand B', 'active')
        ON CONFLICT (brand_id) DO NOTHING
        """,
        (BRAND_ID_B, TENANT_ID_B, f"fongniao_{MERCHANT_ID_B}"),
    )
    conn.execute(
        """
        INSERT INTO core.stores (store_id, tenant_id, brand_id, source_store_id, store_code, store_name, store_status)
        VALUES (%s, %s, %s, %s, 'STORE_02', 'Lifecycle Store 2', 'open')
        ON CONFLICT (store_id) DO NOTHING
        """,
        (STORE_ID_B, TENANT_ID_B, BRAND_ID_B, PLACE_ID_B),
    )
    conn.execute(
        """
        INSERT INTO core.machines (machine_id, store_id, source_machine_id, machine_serial_no, machine_status)
        VALUES (%s, %s, %s, 'SN-002', 'active')
        ON CONFLICT (machine_id) DO NOTHING
        """,
        (MACHINE_ID_B, STORE_ID_B, DEVICE_ID_B),
    )


def _seed_ingestion_run(conn: Any) -> None:
    conn.execute(
        f"""
        INSERT INTO {CONTROL_SCHEMA}.ingestion_runs (
            run_id, source_database, source_kind, partition_key, status,
            processed_count, valid_loaded, quarantined_count,
            reconciled, partition_complete, started_at, finished_at
        ) VALUES (
            %s, 'fongniao_prod', 'device_log', %s, 'SUCCEEDED',
            0, 0, 0,
            TRUE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (run_id) DO NOTHING
        """,
        (RUN_ID, f"device_log:{PLACE_ID}"),
    )


class _FakeLandingDb:
    def __init__(self, intake_db: Any, control_schema: str = CONTROL_SCHEMA) -> None:
        self.control_schema = control_schema
        self._intake = intake_db

    def _connect(self) -> Any:
        return self._intake.connect(autocommit=True)


@pytest.fixture
def lifecycle_pg_db(intake_blank_db, tmp_path):
    """PostgreSQL database initialized with canonical hierarchy, control schema, and migration 000027."""
    mapping_file = tmp_path / "status_mapping.json"
    mapping_file.write_text(STATUS_MAPPING_CONTENT, encoding="utf-8")

    # Pre-cutover schema & hierarchy
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_PRE_CUTOVER_SCHEMA)

    # Install control schema
    config = DataPlaneConfig(
        mongo_uri="mongodb+srv://service:secret@approved.example/fongniao_prod",
        postgres_dsn="postgresql://service:secret@sql.example/oday",
        control_schema=CONTROL_SCHEMA,
        status_mapping_path=mapping_file,
    )
    store = PsycopgCanonicalStore(
        config,
        connection_factory=lambda: intake_blank_db.connect(autocommit=True),
    )
    store.install()

    with intake_blank_db.connect(autocommit=True) as conn:
        _seed_hierarchy(conn)
        _seed_ingestion_run(conn)

    # Apply migration 000027
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(MIGRATION_SQL.read_text(encoding="utf-8"))

    return SimpleNamespace(
        intake=intake_blank_db,
        config=config,
        canonical_store=store,
        landing_db=_FakeLandingDb(intake_blank_db),
    )


# ==============================================================================
# 1. Forward Migration & Pre-cutover Row Compatibility
# ==============================================================================


def test_forward_migration_adds_record_status_and_timestamps_to_preexisting_rows(
    intake_blank_db,
) -> None:
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_PRE_CUTOVER_SCHEMA)
        _seed_hierarchy(conn)

        # Insert pre-cutover rows without record_status / timestamps
        event_id_1 = uuid4()
        event_id_2 = uuid4()
        conn.execute(
            """
            INSERT INTO core.machine_status_events (status_event_id, store_id, machine_id, event_time, status_type, severity)
            VALUES (%s, %s, %s, '2026-09-01 10:00:00+00', 'online', 'info')
            """,
            (event_id_1, STORE_ID, MACHINE_ID),
        )
        conn.execute(
            """
            INSERT INTO core.machine_status_events (status_event_id, store_id, machine_id, event_time, status_type, severity)
            VALUES (%s, %s, %s, '2026-09-01 11:00:00+00', 'error', 'critical')
            """,
            (event_id_2, STORE_ID, MACHINE_ID),
        )

        # Run forward migration 000027
        conn.execute(MIGRATION_SQL.read_text(encoding="utf-8"))

        # Verify column definitions
        columns = conn.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'machine_status_events'
            AND column_name IN ('record_status', 'created_at', 'updated_at')
            ORDER BY column_name;
            """
        ).fetchall()

        cols = {row[0]: (row[1], row[2], row[3]) for row in columns}
        assert "record_status" in cols
        assert "created_at" in cols
        assert "updated_at" in cols

        assert cols["record_status"][1] == "NO"  # NOT NULL
        assert "active" in cols["record_status"][2]

        # Verify pre-cutover rows have record_status = 'active'
        rows = conn.execute(
            """
            SELECT status_event_id, status_type, record_status, created_at, updated_at
            FROM core.machine_status_events
            ORDER BY event_time;
            """
        ).fetchall()

        assert len(rows) == 2
        assert rows[0][1] == "online"
        assert rows[0][2] == "active"
        assert rows[0][3] is not None
        assert rows[0][4] is not None

        assert rows[1][1] == "error"
        assert rows[1][2] == "active"


def test_migration_rollback_and_reapply_idempotency(intake_blank_db) -> None:
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_PRE_CUTOVER_SCHEMA)
        conn.execute(MIGRATION_SQL.read_text(encoding="utf-8"))

        # Rollback statements
        conn.execute(
            """
            ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS record_status;
            ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS created_at;
            ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS updated_at;
            DROP INDEX IF EXISTS core.idx_machine_status_events_record_status;
            """
        )

        # Columns dropped
        cols = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'machine_status_events'
            AND column_name IN ('record_status', 'created_at', 'updated_at');
            """
        ).fetchall()
        assert len(cols) == 0

        # Re-apply migration
        conn.execute(MIGRATION_SQL.read_text(encoding="utf-8"))
        cols_after = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'machine_status_events'
            AND column_name IN ('record_status', 'created_at', 'updated_at');
            """
        ).fetchall()
        assert len(cols_after) == 3


# ==============================================================================
# 2. Lifecycle Transitions: Active -> Soft-Retired & Tombstone Persistence
# ==============================================================================


def test_active_to_soft_retired_lifecycle_transition_and_tombstone(lifecycle_pg_db) -> None:
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    observed_at = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    envelope = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-001"),
        run_id=RUN_ID,
        observed_at=observed_at,
        source_updated_at=observed_at,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {
                "state": "online",
                "time": "2026-09-20T10:00:00Z",
            },
            "createdAt": observed_at.isoformat(),
        },
    )

    # 1. Batch upsert
    result = store.apply_batch(
        SourceKind.DEVICE_LOG, (envelope,), partition_key=f"device_log:{PLACE_ID}"
    )
    assert result.valid_loaded == 1
    assert result.quarantined_count == 0

    with landing._connect() as conn:
        row = conn.execute(
            "SELECT status_type, record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "online"
        assert row[1] == "active"

    # 2. CDC soft-delete event arrives
    delete_time = observed_at + timedelta(minutes=10)
    cdc_envelope = CdcChangeEnvelope(
        change_id="cdc-del-001",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-001",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=1,
        server_timestamp=delete_time,
        source_timestamp=delete_time,
        ingested_at=delete_time,
        idempotency_key="idemp-del-001",
        tenant_id=TENANT_ID,
        content_sha256=_sha256("cdc-del-001"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )

    plan = plan_change_application(cdc_envelope, run_id=RUN_ID, now=delete_time)
    assert plan.soft_delete is not None
    assert plan.soft_delete.canonical_table == "core.machine_status_events"
    assert plan.soft_delete.status_column == "record_status"
    assert plan.soft_delete.status_value == "voided"
    assert plan.tombstone is not None
    assert plan.lifecycle_gap == ""

    # 3. Project via ScopedCdcProjector
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    projection_res = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan])

    assert projection_res.soft_deleted == 1
    assert projection_res.tombstoned == 1
    assert projection_res.delete_outcomes.get("APPLIED") == 1
    assert len(projection_res.lifecycle_gaps) == 0

    # Verify PostgreSQL row state: soft-retired, NOT hard deleted
    with landing._connect() as conn:
        row = conn.execute(
            "SELECT status_type, record_status, updated_at FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "online"  # Device state preserved
        assert row[1] == "voided"  # Row lifecycle state soft-retired
        assert row[2] == delete_time

        # Verify tombstone row
        tombstone = conn.execute(
            f"SELECT entity_type, entity_id, propagation_mode, tenant_id FROM {CONTROL_SCHEMA}.tombstones WHERE entity_id = %s",
            (DEVICE_ID,),
        ).fetchone()
        assert tombstone is not None
        assert tombstone[0] == "device_log"
        assert tombstone[1] == DEVICE_ID
        assert tombstone[2] == "TOMBSTONE_PURGE"
        assert tombstone[3] == TENANT_ID


# ==============================================================================
# 3. Guarding Against Resurrection & Stale Replay
# ==============================================================================


def test_stale_batch_replay_cannot_resurrect_soft_deleted_row(lifecycle_pg_db) -> None:
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    initial_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    delete_time = datetime(2026, 9, 20, 10, 15, 0, tzinfo=UTC)

    # Ingest and soft-delete
    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-001"),
        run_id=RUN_ID,
        observed_at=initial_time,
        source_updated_at=initial_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    cdc_envelope = CdcChangeEnvelope(
        change_id="cdc-del-002",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-002",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=2,
        server_timestamp=delete_time,
        source_timestamp=delete_time,
        ingested_at=delete_time,
        idempotency_key="idemp-del-002",
        tenant_id=TENANT_ID,
        content_sha256=_sha256("cdc-del-002"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )
    plan = plan_change_application(cdc_envelope, run_id=RUN_ID, now=delete_time)
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan])

    # Replay stale / older batch envelope
    stale_env = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-stale"),
        run_id=RUN_ID,
        observed_at=datetime(2026, 9, 20, 10, 20, 0, tzinfo=UTC),
        source_updated_at=initial_time,  # Older version <= tombstone version
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )

    replay_result = store.apply_batch(
        SourceKind.DEVICE_LOG, (stale_env,), partition_key=f"device_log:{PLACE_ID}"
    )
    assert replay_result.valid_loaded == 0
    assert replay_result.quarantined_count == 1
    assert replay_result.quarantine_reason_counts.get("SOURCE_DELETED") == 1

    # Ensure row is still voided and not resurrected
    with landing._connect() as conn:
        row = conn.execute(
            "SELECT record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "voided"


def test_newer_event_updates_and_reactivates_soft_retired_row(lifecycle_pg_db) -> None:
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    initial_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    delete_time = datetime(2026, 9, 20, 10, 15, 0, tzinfo=UTC)
    newer_time = datetime(2026, 9, 20, 11, 0, 0, tzinfo=UTC)

    # 1. Ingest initial and delete
    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-001"),
        run_id=RUN_ID,
        observed_at=initial_time,
        source_updated_at=initial_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    cdc_del = CdcChangeEnvelope(
        change_id="cdc-del-003",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-003",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=3,
        server_timestamp=delete_time,
        source_timestamp=delete_time,
        ingested_at=delete_time,
        idempotency_key="idemp-del-003",
        tenant_id=TENANT_ID,
        content_sha256=_sha256("cdc-del-003"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )
    plan_del = plan_change_application(cdc_del, run_id=RUN_ID, now=delete_time)
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan_del])

    # 2. Genuinely newer event arrives (newer source_updated_at > tombstone version)
    newer_env = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-newer"),
        run_id=RUN_ID,
        observed_at=newer_time,
        source_updated_at=newer_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "available", "time": newer_time.isoformat()},
            "createdAt": newer_time.isoformat(),
        },
    )

    result = store.apply_batch(
        SourceKind.DEVICE_LOG, (newer_env,), partition_key=f"device_log:{PLACE_ID}"
    )
    assert result.valid_loaded == 1
    assert result.quarantined_count == 0

    with landing._connect() as conn:
        row = conn.execute(
            "SELECT status_type, record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "available"
        assert row[1] == "active"


# ==============================================================================
# 4. Tenant & Version Guards on Soft Delete
# ==============================================================================


def test_tenant_boundary_and_out_of_order_delete_guards(lifecycle_pg_db) -> None:
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    initial_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    older_delete_time = datetime(2026, 9, 20, 9, 0, 0, tzinfo=UTC)  # Older than ingested event

    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-001"),
        run_id=RUN_ID,
        observed_at=initial_time,
        source_updated_at=initial_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    # Out-of-order older delete event
    older_cdc = CdcChangeEnvelope(
        change_id="cdc-del-older",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-older",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=1,
        server_timestamp=older_delete_time,
        source_timestamp=older_delete_time,
        ingested_at=initial_time,
        idempotency_key="idemp-del-older",
        tenant_id=TENANT_ID,
        content_sha256=_sha256("cdc-del-older"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )

    plan = plan_change_application(older_cdc, run_id=RUN_ID, now=initial_time)
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    res = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan])

    # Because older delete source_version < lineage source_version:
    # 1. SoftDeleteDirective WHERE clause lineage.source_version <= %s evaluates to False -> 0 rows updated
    assert res.soft_deleted == 0
    # 2. Tombstone outcome is STALE_IGNORED
    assert res.delete_outcomes.get("STALE_IGNORED") == 1

    # Row remains active
    with landing._connect() as conn:
        row = conn.execute(
            "SELECT record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "active"


def test_duplicate_cdc_delete_is_idempotent(lifecycle_pg_db) -> None:
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    initial_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    delete_time = datetime(2026, 9, 20, 10, 30, 0, tzinfo=UTC)

    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-001"),
        run_id=RUN_ID,
        observed_at=initial_time,
        source_updated_at=initial_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    cdc_del = CdcChangeEnvelope(
        change_id="cdc-del-dup",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-dup",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=10,
        server_timestamp=delete_time,
        source_timestamp=delete_time,
        ingested_at=delete_time,
        idempotency_key="idemp-del-dup",
        tenant_id=TENANT_ID,
        content_sha256=_sha256("cdc-del-dup"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )

    plan1 = plan_change_application(cdc_del, run_id=RUN_ID, now=delete_time)
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    res1 = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan1])
    assert res1.soft_deleted == 1
    assert res1.delete_outcomes.get("APPLIED") == 1

    # Replay same delete
    plan2 = plan_change_application(cdc_del, run_id=RUN_ID, now=delete_time)
    res2 = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan2])
    assert res2.soft_deleted == 0  # Already voided (IS DISTINCT FROM 'voided' is False)
    assert res2.delete_outcomes.get("REPLAYED") == 1

    with landing._connect() as conn:
        row = conn.execute(
            "SELECT record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "voided"


def test_historical_lineage_stale_delete_after_reactivation_does_not_void_row(
    lifecycle_pg_db,
) -> None:
    """Historical lineage row must not cause a stale delete replay to void an active reactivated row."""
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    t1 = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)   # v10
    t2 = datetime(2026, 9, 20, 10, 20, 0, tzinfo=UTC)  # v20 (delete)
    t3 = datetime(2026, 9, 20, 10, 40, 0, tzinfo=UTC)  # v30 (reactivate)

    # 1. Upsert v10 -> active
    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-v10"),
        run_id=RUN_ID,
        observed_at=t1,
        source_updated_at=t1,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": t1.isoformat()},
            "createdAt": t1.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    # 2. Delete v20 -> voided
    cdc_del_v20 = CdcChangeEnvelope(
        change_id="cdc-del-v20",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-v20",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=2,
        server_timestamp=t2,
        source_timestamp=t2,
        ingested_at=t2,
        idempotency_key="idemp-del-v20",
        tenant_id=TENANT_ID,
        content_sha256=_sha256("cdc-del-v20"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    plan_v20 = plan_change_application(cdc_del_v20, run_id=RUN_ID, now=t2)
    res_del = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan_v20])
    assert res_del.soft_deleted == 1
    assert res_del.delete_outcomes.get("APPLIED") == 1

    with landing._connect() as conn:
        row = conn.execute(
            "SELECT record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "voided"

    # 3. Newer upsert v30 -> reactivates row to active
    env3 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-v30"),
        run_id=RUN_ID,
        observed_at=t3,
        source_updated_at=t3,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "available", "time": t3.isoformat()},
            "createdAt": t3.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env3,), partition_key=f"device_log:{PLACE_ID}")

    with landing._connect() as conn:
        row = conn.execute(
            "SELECT status_type, record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "available"
        assert row[1] == "active"

    # 4. Replay stale delete v20 (even though lineage table retains both v10 and v30)
    res_stale = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan_v20])
    assert res_stale.soft_deleted == 0
    assert res_stale.delete_outcomes.get("STALE_IGNORED") == 1

    # Row must remain active!
    with landing._connect() as conn:
        row = conn.execute(
            "SELECT status_type, record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "available"
        assert row[1] == "active"


def test_delete_without_tenant_resolved_from_lineage_soft_deletes_and_tombstones(
    lifecycle_pg_db,
) -> None:
    """CDC delete with tenant_id=None resolves tenant from lineage under lock and soft deletes atomically."""
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    initial_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    delete_time = datetime(2026, 9, 20, 10, 30, 0, tzinfo=UTC)

    # 1. Ingest event with known merchant/tenant
    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-inferred-test"),
        run_id=RUN_ID,
        observed_at=initial_time,
        source_updated_at=initial_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    # 2. CDC delete packet without fullDocument (tenant_id=None, after_payload=None)
    cdc_del_no_tenant = CdcChangeEnvelope(
        change_id="cdc-del-no-tenant",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-no-tenant",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=1,
        server_timestamp=delete_time,
        source_timestamp=delete_time,
        ingested_at=delete_time,
        idempotency_key="idemp-del-no-tenant",
        tenant_id=None,  # No tenant declared in change stream
        content_sha256=_sha256("cdc-del-no-tenant"),
        after_payload=None,
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )

    plan = plan_change_application(cdc_del_no_tenant, run_id=RUN_ID, now=delete_time)
    assert plan.soft_delete is None  # SoftDeleteDirective not constructed up-front because tenant is None
    assert plan.tombstone is not None
    assert plan.tombstone.scope.tenant_id is None

    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    res = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan])

    assert res.soft_deleted == 1
    assert res.tombstoned == 1
    assert res.delete_outcomes.get("APPLIED") == 1

    # Check that row is voided
    with landing._connect() as conn:
        row = conn.execute(
            "SELECT status_type, record_status, updated_at FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "online"
        assert row[1] == "voided"
        assert row[2] == delete_time

        # Check tombstone recorded under the inferred tenant
        tomb = conn.execute(
            f"SELECT entity_type, entity_id, tenant_id FROM {CONTROL_SCHEMA}.tombstones WHERE entity_id = %s",
            (DEVICE_ID,),
        ).fetchone()
        assert tomb is not None
        assert tomb[0] == "device_log"
        assert tomb[1] == DEVICE_ID
        assert tomb[2] == TENANT_ID


def test_cross_tenant_delete_rejected_and_does_not_mutate_row(lifecycle_pg_db) -> None:
    """A delete claiming Tenant B against an entity belonging to Tenant A is rejected."""
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    initial_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    delete_time = datetime(2026, 9, 20, 10, 30, 0, tzinfo=UTC)

    # 1. Ingest event under Tenant A
    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-tenant-a"),
        run_id=RUN_ID,
        observed_at=initial_time,
        source_updated_at=initial_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,  # Tenant A
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    # 2. CDC delete claiming Tenant B
    cdc_del_wrong_tenant = CdcChangeEnvelope(
        change_id="cdc-del-wrong-tenant",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-wrong-tenant",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=1,
        server_timestamp=delete_time,
        source_timestamp=delete_time,
        ingested_at=delete_time,
        idempotency_key="idemp-del-wrong-tenant",
        tenant_id=TENANT_ID_B,  # Declares Tenant B!
        content_sha256=_sha256("cdc-del-wrong-tenant"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID_B},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )

    plan = plan_change_application(cdc_del_wrong_tenant, run_id=RUN_ID, now=delete_time)
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )
    res = projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan])

    assert res.soft_deleted == 0
    assert res.delete_outcomes.get("REJECTED_TENANT_BOUNDARY") == 1

    # Row in Tenant A remains active
    with landing._connect() as conn:
        row = conn.execute(
            "SELECT record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "active"

        # No tombstone recorded
        tomb_count = conn.execute(
            f"SELECT COUNT(*) FROM {CONTROL_SCHEMA}.tombstones WHERE entity_id = %s",
            (DEVICE_ID,),
        ).fetchone()[0]
        assert tomb_count == 0


def test_delete_transaction_rollback_prevents_half_applied_retirement(
    lifecycle_pg_db, monkeypatch
) -> None:
    """If tombstone upsert fails inside the canonical transaction, soft-delete is rolled back."""
    store: PsycopgCanonicalStore = lifecycle_pg_db.canonical_store
    landing = lifecycle_pg_db.landing_db

    initial_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    delete_time = datetime(2026, 9, 20, 10, 30, 0, tzinfo=UTC)

    # Ingest event
    env1 = SourceEnvelope(
        source_kind=SourceKind.DEVICE_LOG,
        source_id=DEVICE_ID,
        source_snapshot_id=str(uuid4()),
        content_sha256=_sha256("device-log-rollback-test"),
        run_id=RUN_ID,
        observed_at=initial_time,
        source_updated_at=initial_time,
        source_document={
            "_id": DEVICE_ID,
            "device": DEVICE_ID,
            "merchant": MERCHANT_ID,
            "place": PLACE_ID,
            "logType": "connection",
            "logData": {"state": "online", "time": initial_time.isoformat()},
            "createdAt": initial_time.isoformat(),
        },
    )
    store.apply_batch(SourceKind.DEVICE_LOG, (env1,), partition_key=f"device_log:{PLACE_ID}")

    # Patch _upsert_tombstone to simulate a failure right after soft-delete execution
    def failing_upsert(*args, **kwargs):
        raise RuntimeError("Simulated database failure during tombstone insertion")

    monkeypatch.setattr(store, "_upsert_tombstone", failing_upsert)

    cdc_del = CdcChangeEnvelope(
        change_id="cdc-del-fail",
        source_kind=SourceKind.DEVICE_LOG,
        source_collection="device_log",
        source_id=DEVICE_ID,
        operation=CdcOperation.DELETE,
        resume_token="token-del-fail",
        partition_key=f"device_log:{DEVICE_ID}",
        sequence_number=1,
        server_timestamp=delete_time,
        source_timestamp=delete_time,
        ingested_at=delete_time,
        idempotency_key="idemp-del-fail",
        tenant_id=TENANT_ID,
        content_sha256=_sha256("cdc-del-fail"),
        after_payload={"_id": DEVICE_ID, "device": DEVICE_ID, "merchant": MERCHANT_ID},
        redaction=RedactionReport(cdc_policy(SourceKind.DEVICE_LOG).redaction_profile, (), ()),
    )
    plan = plan_change_application(cdc_del, run_id=RUN_ID, now=delete_time)
    projector = ScopedCdcProjector(
        canonical_store=store, landing_store=landing, control_schema=CONTROL_SCHEMA
    )

    with pytest.raises(RuntimeError, match="Simulated database failure"):
        projector.apply(SourceKind.DEVICE_LOG, f"device_log:{DEVICE_ID}", [plan])

    # Verify that the row remains active (transaction rolled back, no half-applied state)
    with landing._connect() as conn:
        row = conn.execute(
            "SELECT record_status FROM core.machine_status_events WHERE machine_id = %s",
            (MACHINE_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "active"

        tomb_count = conn.execute(
            f"SELECT COUNT(*) FROM {CONTROL_SCHEMA}.tombstones WHERE entity_id = %s",
            (DEVICE_ID,),
        ).fetchone()[0]
        assert tomb_count == 0


# ==============================================================================
# 5. Alembic Version Migration Test
# ==============================================================================


def test_alembic_migration_0021_upgrade_and_downgrade(intake_blank_db) -> None:
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_PRE_CUTOVER_SCHEMA)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.alembic_version (
                version_num VARCHAR(32) PRIMARY KEY
            );
            INSERT INTO public.alembic_version (version_num) VALUES ('0020')
            ON CONFLICT (version_num) DO NOTHING;
            """
        )

    from alembic import command
    from alembic.config import Config

    alembic_ini_path = REPO_ROOT / "infra/db/migrations/alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "infra/db/migrations"))
    alembic_cfg.set_main_option(
        "sqlalchemy.url", intake_blank_db.url(driver="psycopg").replace("%", "%%")
    )

    command.upgrade(alembic_cfg, "0021")

    with intake_blank_db.connect(autocommit=True) as conn:
        cols = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'machine_status_events'
            AND column_name IN ('record_status', 'created_at', 'updated_at');
            """
        ).fetchall()
        assert len(cols) == 3

    command.downgrade(alembic_cfg, "0020")

    with intake_blank_db.connect(autocommit=True) as conn:
        cols = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'machine_status_events'
            AND column_name IN ('record_status', 'created_at', 'updated_at');
            """
        ).fetchall()
        assert len(cols) == 0
