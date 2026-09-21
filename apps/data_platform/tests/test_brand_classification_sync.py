"""Brand classification preservation tests during merchant synchronization.

Scope:
- Verifies that merchant sync (_upsert_merchant / apply_batch) does NOT overwrite
  existing brand classifications (external, franchise, competitor, owned) with 'owned'.
- Verifies that other projected attributes (companyName -> brand_name/tenant_name,
  operation -> status, updated_at) ARE updated as expected.
- Verifies that new brands without prior classification continue to default to 'owned'
  per initial contract requirements.
- Verifies replay idempotency and tenant/brand identity isolation on real PostgreSQL persistence.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from apps.data_platform.contracts import SourceKind
from apps.data_platform.identifiers import (
    brand_id_for_merchant,
    tenant_id_for_merchant,
)
from apps.data_platform.source import envelope_for_document
from apps.data_platform.store import PsycopgCanonicalStore

CONTROL_SCHEMA = "data_plane"
OBSERVED_AT = datetime(2026, 7, 24, 12, tzinfo=UTC)

_MINIMAL_CORE_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE core.tenants (
    tenant_id UUID PRIMARY KEY,
    tenant_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE core.brands (
    brand_id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES core.tenants(tenant_id),
    brand_code TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    brand_type TEXT NOT NULL,
    brand_capture_group TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, brand_id)
);
CREATE TABLE core.stores (
    store_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    brand_id UUID REFERENCES core.brands(brand_id),
    source_store_id TEXT
);
CREATE TABLE core.machines (
    machine_id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    source_machine_id TEXT
);
CREATE TABLE core.transactions (
    transaction_id UUID PRIMARY KEY,
    source_transaction_id TEXT,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID REFERENCES core.machines(machine_id),
    member_id UUID,
    event_time TIMESTAMPTZ,
    observation_time TIMESTAMPTZ,
    payment_time TIMESTAMPTZ,
    gross_amount NUMERIC,
    discount_amount NUMERIC,
    net_amount NUMERIC,
    currency TEXT,
    payment_method TEXT,
    transaction_status TEXT,
    refund_of_transaction_id UUID REFERENCES core.transactions(transaction_id),
    price_schedule_id UUID,
    promotion_id UUID,
    source_system TEXT,
    ingested_at TIMESTAMPTZ
);
CREATE TABLE core.machine_cycles (
    cycle_id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    transaction_id UUID REFERENCES core.transactions(transaction_id),
    cycle_start_time TIMESTAMPTZ NOT NULL,
    cycle_end_time TIMESTAMPTZ NOT NULL
);
CREATE TABLE core.machine_status_events (
    status_event_id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    event_time TIMESTAMPTZ NOT NULL
);
"""


@pytest.fixture(scope="session")
def _pg_admin_params():
    psycopg = pytest.importorskip(
        "psycopg", reason="brand classification sync tests need the psycopg driver"
    )
    dsn = os.environ.get("INTAKE_TEST_DATABASE_URL")
    if dsn:
        admin = psycopg.conninfo.conninfo_to_dict(dsn)
        admin.setdefault("dbname", "postgres")
        try:
            psycopg.connect(autocommit=True, **admin).close()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"INTAKE_TEST_DATABASE_URL unreachable: {exc}")
        yield psycopg, admin
        return

    pgserver = pytest.importorskip(
        "pgserver",
        reason="No INTAKE_TEST_DATABASE_URL and pgserver (bundled PostgreSQL 16) unavailable",
    )
    data_dir = tempfile.mkdtemp(prefix="data-plane-brand-sync-pg16-")
    server = pgserver.get_server(data_dir)
    try:
        host = re.search(r"host=([^&]+)", server.get_uri()).group(1)
        yield psycopg, {"host": host, "dbname": "postgres", "user": "postgres"}
    finally:
        try:
            server.cleanup()
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
def live_store(_pg_admin_params):
    """A canonical store over a disposable PostgreSQL database with control schema."""
    psycopg, admin = _pg_admin_params
    dbname = f"dp_brand_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(autocommit=True, **admin) as admin_connection:
        admin_connection.execute(f'CREATE DATABASE "{dbname}"')
    params = {**admin, "dbname": dbname}

    def connect():
        return psycopg.connect(**params)

    def build_store() -> PsycopgCanonicalStore:
        store = object.__new__(PsycopgCanonicalStore)
        store._config = SimpleNamespace(
            control_schema=CONTROL_SCHEMA,
            raw_schema="fongniao_raw",
        )
        store._status_contract = None
        store._connect = connect
        return store

    try:
        with connect() as connection:
            connection.execute(_MINIMAL_CORE_SCHEMA)
        build_store().install()
        yield SimpleNamespace(build=build_store, connect=connect, store=build_store())
    finally:
        with psycopg.connect(autocommit=True, **admin) as admin_connection:
            admin_connection.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _merchant_doc(
    source_id: str,
    *,
    name: str = "Test Merchant",
    operation: str = "active",
) -> dict[str, Any]:
    return {
        "_id": source_id,
        "companyName": name,
        "country": "TW",
        "currency": "TWD",
        "operation": operation,
        "createdAt": "2026-07-01T00:00:00Z",
    }


def _envelope(
    kind: SourceKind,
    document: dict[str, Any],
    *,
    run_id: str = "00000000-0000-4000-8000-000000000001",
    observed_at: datetime = OBSERVED_AT,
):
    return envelope_for_document(
        kind,
        document,
        run_id=run_id,
        observed_at=observed_at,
    )


def _apply_merchant_batch(
    live_store: Any,
    envelopes: list[Any],
    *,
    partition_key: str = "2026-07-24",
    run_id: str = "00000000-0000-4000-8000-000000000001",
):
    live_store.store.begin_run(
        run_id, SourceKind.MERCHANT, partition_key, None, OBSERVED_AT
    )
    return live_store.store.apply_batch(
        SourceKind.MERCHANT, envelopes, partition_key=partition_key
    )


def _seed_brand(
    connection: Any,
    *,
    source_merchant_id: str,
    brand_type: str,
    brand_name: str = "Initial Brand Name",
    brand_capture_group: str = "custom_group",
    status: str = "active",
) -> tuple[UUID, UUID]:
    tenant_id = tenant_id_for_merchant(source_merchant_id)
    brand_id = brand_id_for_merchant(source_merchant_id)
    brand_code = f"fongniao_{source_merchant_id}"
    connection.execute(
        """
        INSERT INTO core.tenants (tenant_id, tenant_name, status)
        VALUES (%s, %s, %s)
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        (tenant_id, f"Initial Tenant {source_merchant_id}", status),
    )
    connection.execute(
        """
        INSERT INTO core.brands (
            brand_id, tenant_id, brand_code, brand_name, brand_type,
            brand_capture_group, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (brand_id, tenant_id, brand_code, brand_name, brand_type, brand_capture_group, status),
    )
    return tenant_id, brand_id


def test_sync_preserves_preexisting_brand_classifications_external_franchise_competitor_owned(
    live_store,
) -> None:
    """Merchant sync updates names and status but MUST preserve brand_type for all classifications."""
    test_classifications = ["external", "franchise", "competitor", "owned"]

    with live_store.connect() as conn:
        for brand_type in test_classifications:
            m_id = f"merchant-{brand_type}"
            _seed_brand(
                conn,
                source_merchant_id=m_id,
                brand_type=brand_type,
                brand_name=f"Old Name {brand_type}",
            )

    envelopes = [
        _envelope(
            SourceKind.MERCHANT,
            _merchant_doc(
                f"merchant-{b_type}",
                name=f"Updated Name {b_type}",
                operation="active",
            ),
        )
        for b_type in test_classifications
    ]

    result = _apply_merchant_batch(live_store, envelopes)
    assert result.valid_loaded == len(test_classifications)
    assert result.quarantine_reason_counts == {}

    with live_store.connect() as conn:
        for brand_type in test_classifications:
            m_id = f"merchant-{brand_type}"
            brand_id = brand_id_for_merchant(m_id)
            tenant_id = tenant_id_for_merchant(m_id)

            row = conn.execute(
                """
                SELECT brand_id, tenant_id, brand_code, brand_name, brand_type, status
                FROM core.brands
                WHERE brand_id = %s
                """,
                (brand_id,),
            ).fetchone()

            assert row is not None
            assert row[0] == brand_id
            assert row[1] == tenant_id
            assert row[2] == f"fongniao_{m_id}"
            assert row[3] == f"Updated Name {brand_type}", "brand_name should be updated"
            assert row[4] == brand_type, f"brand_type for {brand_type} must be preserved!"
            assert row[5] == "active"

            t_row = conn.execute(
                "SELECT tenant_name, status FROM core.tenants WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchone()
            assert t_row is not None
            assert t_row[0] == f"Updated Name {brand_type}"
            assert t_row[1] == "active"


def test_new_brand_without_prior_classification_defaults_to_owned(live_store) -> None:
    """When a new merchant is synced with no prior brand record, brand_type defaults to 'owned'."""
    m_id = "new-brand-merchant-1"
    tenant_id = tenant_id_for_merchant(m_id)
    brand_id = brand_id_for_merchant(m_id)

    envelope = _envelope(
        SourceKind.MERCHANT,
        _merchant_doc(m_id, name="Fresh Brand Store"),
    )

    result = _apply_merchant_batch(live_store, [envelope])
    assert result.valid_loaded == 1

    with live_store.connect() as conn:
        row = conn.execute(
            """
            SELECT brand_id, tenant_id, brand_code, brand_name, brand_type, status
            FROM core.brands
            WHERE brand_id = %s
            """,
            (brand_id,),
        ).fetchone()

        assert row is not None
        assert row[0] == brand_id
        assert row[1] == tenant_id
        assert row[2] == f"fongniao_{m_id}"
        assert row[3] == "Fresh Brand Store"
        assert row[4] == "owned", "new brand should default to owned"
        assert row[5] == "active"


def test_sync_replay_and_idempotency_preserves_classification(live_store) -> None:
    """Replaying merchant sync multiple times is idempotent and never changes classification."""
    m_id = "replay-competitor-merchant"
    brand_id = brand_id_for_merchant(m_id)

    with live_store.connect() as conn:
        _seed_brand(
            conn,
            source_merchant_id=m_id,
            brand_type="competitor",
            brand_name="Competitor Brand Original",
        )

    envelope_v1 = _envelope(
        SourceKind.MERCHANT,
        _merchant_doc(m_id, name="Competitor Brand Run 1"),
    )
    envelope_v2 = _envelope(
        SourceKind.MERCHANT,
        _merchant_doc(m_id, name="Competitor Brand Run 2"),
    )

    # First sync run
    res1 = _apply_merchant_batch(
        live_store, [envelope_v1], run_id="00000000-0000-4000-8000-000000000001"
    )
    assert res1.valid_loaded == 1

    with live_store.connect() as conn:
        row1 = conn.execute(
            "SELECT brand_type, brand_name FROM core.brands WHERE brand_id = %s",
            (brand_id,),
        ).fetchone()
        assert row1 == ("competitor", "Competitor Brand Run 1")

    # Replay sync run with updated name
    res2 = _apply_merchant_batch(
        live_store, [envelope_v2], run_id="00000000-0000-4000-8000-000000000002"
    )
    assert res2.valid_loaded == 1

    with live_store.connect() as conn:
        row2 = conn.execute(
            "SELECT brand_type, brand_name FROM core.brands WHERE brand_id = %s",
            (brand_id,),
        ).fetchone()
        assert row2 == ("competitor", "Competitor Brand Run 2")

    # Duplicate replay with same payload
    res3 = _apply_merchant_batch(
        live_store, [envelope_v2], run_id="00000000-0000-4000-8000-000000000003"
    )
    assert res3.valid_loaded == 1

    with live_store.connect() as conn:
        row3 = conn.execute(
            "SELECT brand_type, brand_name FROM core.brands WHERE brand_id = %s",
            (brand_id,),
        ).fetchone()
        assert row3 == ("competitor", "Competitor Brand Run 2")


def test_sync_updates_status_transitions_without_altering_brand_type(live_store) -> None:
    """Status changes (active -> inactive / disabled) update status while keeping brand_type."""
    m_id = "franchise-status-change"
    tenant_id = tenant_id_for_merchant(m_id)
    brand_id = brand_id_for_merchant(m_id)

    with live_store.connect() as conn:
        _seed_brand(
            conn,
            source_merchant_id=m_id,
            brand_type="franchise",
            brand_name="Franchise Store",
            status="active",
        )

    # Inactivate merchant
    envelope_inactive = _envelope(
        SourceKind.MERCHANT,
        _merchant_doc(m_id, name="Franchise Store Closed", operation="disabled"),
    )
    res = _apply_merchant_batch(live_store, [envelope_inactive])
    assert res.valid_loaded == 1

    with live_store.connect() as conn:
        row = conn.execute(
            "SELECT brand_type, brand_name, status FROM core.brands WHERE brand_id = %s",
            (brand_id,),
        ).fetchone()
        assert row == ("franchise", "Franchise Store Closed", "inactive")

        t_row = conn.execute(
            "SELECT status FROM core.tenants WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()
        assert t_row == ("inactive",)


def test_tenant_and_brand_identity_isolation_across_multiple_entities(live_store) -> None:
    """Updates to one merchant do not mutate or cross-contaminate another's classification or identity."""
    m1 = "merchant-iso-1"
    m2 = "merchant-iso-2"

    with live_store.connect() as conn:
        _seed_brand(conn, source_merchant_id=m1, brand_type="external", brand_name="External Brand")
        _seed_brand(conn, source_merchant_id=m2, brand_type="competitor", brand_name="Competitor Brand")

    # Update only m1
    envelope_m1 = _envelope(
        SourceKind.MERCHANT,
        _merchant_doc(m1, name="External Brand Renamed"),
    )
    res = _apply_merchant_batch(live_store, [envelope_m1])
    assert res.valid_loaded == 1

    with live_store.connect() as conn:
        b1 = conn.execute(
            "SELECT brand_type, brand_name FROM core.brands WHERE brand_id = %s",
            (brand_id_for_merchant(m1),),
        ).fetchone()
        b2 = conn.execute(
            "SELECT brand_type, brand_name FROM core.brands WHERE brand_id = %s",
            (brand_id_for_merchant(m2),),
        ).fetchone()

        assert b1 == ("external", "External Brand Renamed")
        assert b2 == ("competitor", "Competitor Brand")
