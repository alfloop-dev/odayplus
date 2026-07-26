from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import pytest

MODEL_READY_SQL = (
    Path(__file__).parents[2] / "scripts/models/sql/model_ready_views.sql"
).read_text(encoding="utf-8")
NAMESPACE = UUID("f58c8d6c-baa1-4e58-9984-383fb725cd8a")
ORIGIN = date(2025, 4, 1)
H3_INDEX = "89263064c2fffff"

pytestmark = pytest.mark.requires_live_env


def _id(value: str) -> UUID:
    return uuid5(NAMESPACE, value)


def _at(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour=hour), tzinfo=UTC)


def _install_minimal_authoritative_schema(connection) -> None:
    connection.execute(
        """
        CREATE SCHEMA core;
        CREATE SCHEMA data_plane;

        CREATE TABLE core.address_locations (
            address_id UUID PRIMARY KEY,
            city TEXT,
            district TEXT,
            latitude NUMERIC,
            longitude NUMERIC,
            geocode_confidence NUMERIC,
            h3_res_9 TEXT
        );

        CREATE TABLE core.stores (
            store_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            store_format_code TEXT,
            opened_on DATE,
            address_id UUID
        );

        CREATE TABLE core.transactions (
            transaction_id UUID PRIMARY KEY,
            store_id UUID NOT NULL,
            event_time TIMESTAMPTZ NOT NULL,
            observation_time TIMESTAMPTZ NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL,
            net_amount NUMERIC NOT NULL,
            currency TEXT NOT NULL,
            transaction_status TEXT NOT NULL
        );

        CREATE TABLE data_plane.ingestion_runs (
            run_id UUID PRIMARY KEY,
            source_kind TEXT NOT NULL,
            partition_key TEXT NOT NULL,
            status TEXT NOT NULL,
            finished_at TIMESTAMPTZ
        );

        CREATE TABLE data_plane.canonical_lineage (
            source_snapshot_id UUID NOT NULL,
            run_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            canonical_table TEXT NOT NULL,
            canonical_id UUID NOT NULL,
            projected_at TIMESTAMPTZ NOT NULL
        );
        """
    )


def _insert_run(
    connection,
    *,
    name: str,
    source_kind: str,
    partition_key: str,
    finished_at: datetime,
) -> UUID:
    run_id = _id(f"run:{name}")
    connection.execute(
        """
        INSERT INTO data_plane.ingestion_runs (
            run_id, source_kind, partition_key, status, finished_at
        ) VALUES (%s, %s, %s, 'SUCCEEDED', %s)
        """,
        (run_id, source_kind, partition_key, finished_at),
    )
    return run_id


def _insert_lineage(
    connection,
    *,
    name: str,
    run_id: UUID,
    tenant_id: UUID,
    table: str,
    entity_id: UUID,
    projected_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO data_plane.canonical_lineage (
            source_snapshot_id, run_id, tenant_id, canonical_table,
            canonical_id, projected_at
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (_id(f"snapshot:{name}"), run_id, tenant_id, table, entity_id, projected_at),
    )


def _insert_store(
    connection,
    *,
    tenant: str,
    store: str,
    opened_on: date,
) -> tuple[UUID, UUID, UUID]:
    tenant_id = _id(f"tenant:{tenant}")
    store_id = _id(f"store:{tenant}:{store}")
    address_id = _id(f"address:{tenant}:{store}")
    connection.execute(
        """
        INSERT INTO core.address_locations (
            address_id, city, district, latitude, longitude,
            geocode_confidence, h3_res_9
        ) VALUES (%s, 'Taipei', 'Xinyi', 25.033, 121.565, 0.98, %s)
        """,
        (address_id, H3_INDEX),
    )
    connection.execute(
        """
        INSERT INTO core.stores (
            store_id, tenant_id, store_format_code, opened_on, address_id
        ) VALUES (%s, %s, 'STANDARD', %s, %s)
        """,
        (store_id, tenant_id, opened_on, address_id),
    )
    identity_run = _insert_run(
        connection,
        name=f"identity:{tenant}:{store}",
        source_kind="place",
        partition_key="2024-01-01",
        finished_at=_at(date(2024, 1, 1), 12),
    )
    for table, entity_id in (
        ("core.stores", store_id),
        ("core.address_locations", address_id),
    ):
        _insert_lineage(
            connection,
            name=f"{tenant}:{store}:{table}",
            run_id=identity_run,
            tenant_id=tenant_id,
            table=table,
            entity_id=entity_id,
            projected_at=_at(date(2024, 1, 1), 11),
        )
    return tenant_id, store_id, address_id


def _insert_transaction(
    connection,
    *,
    tenant_id: UUID,
    store_id: UUID,
    name: str,
    event_time: datetime,
    amount: float,
    daily_runs: dict[date, UUID],
) -> UUID:
    transaction_id = _id(f"transaction:{name}")
    observation_time = event_time + timedelta(hours=1)
    ingested_at = event_time + timedelta(hours=2)
    connection.execute(
        """
        INSERT INTO core.transactions (
            transaction_id, store_id, event_time, observation_time,
            ingested_at, net_amount, currency, transaction_status
        ) VALUES (%s, %s, %s, %s, %s, %s, 'TWD', 'succeeded')
        """,
        (
            transaction_id,
            store_id,
            event_time,
            observation_time,
            ingested_at,
            amount,
        ),
    )
    _insert_lineage(
        connection,
        name=name,
        run_id=daily_runs[event_time.date()],
        tenant_id=tenant_id,
        table="core.transactions",
        entity_id=transaction_id,
        projected_at=event_time + timedelta(hours=3),
    )
    return transaction_id


def _seed_point_in_time_history(connection) -> tuple[UUID, UUID]:
    daily_runs: dict[date, UUID] = {}
    first_day = ORIGIN - timedelta(days=90)
    for offset in range(180):
        day = first_day + timedelta(days=offset)
        daily_runs[day] = _insert_run(
            connection,
            name=f"orders:{day.isoformat()}",
            source_kind="orders",
            partition_key=day.isoformat(),
            finished_at=_at(day, 23),
        )

    tenant_a, prior_a, _ = _insert_store(
        connection,
        tenant="a",
        store="prior",
        opened_on=date(2024, 1, 1),
    )
    _, target_a, _ = _insert_store(
        connection,
        tenant="a",
        store="target",
        opened_on=ORIGIN,
    )
    _insert_transaction(
        connection,
        tenant_id=tenant_a,
        store_id=prior_a,
        name="a-prior",
        event_time=_at(date(2025, 3, 1), 10),
        amount=300.0,
        daily_runs=daily_runs,
    )
    _insert_transaction(
        connection,
        tenant_id=tenant_a,
        store_id=target_a,
        name="a-label",
        event_time=_at(date(2025, 4, 15), 10),
        amount=900.0,
        daily_runs=daily_runs,
    )

    tenant_b, prior_b, _ = _insert_store(
        connection,
        tenant="b",
        store="prior",
        opened_on=date(2024, 1, 1),
    )
    _, target_b, _ = _insert_store(
        connection,
        tenant="b",
        store="target",
        opened_on=ORIGIN,
    )
    _insert_transaction(
        connection,
        tenant_id=tenant_b,
        store_id=prior_b,
        name="b-prior",
        event_time=_at(date(2025, 3, 1), 10),
        amount=100_000.0,
        daily_runs=daily_runs,
    )
    _insert_transaction(
        connection,
        tenant_id=tenant_b,
        store_id=target_b,
        name="b-label",
        event_time=_at(date(2025, 4, 15), 10),
        amount=200_000.0,
        daily_runs=daily_runs,
    )
    return tenant_a, target_a


def test_postgresql_views_compute_real_causal_labels_and_isolate_tenants(
    intake_blank_db,
) -> None:
    with intake_blank_db.connect() as connection:
        _install_minimal_authoritative_schema(connection)
        tenant_a, target_a = _seed_point_in_time_history(connection)
        connection.execute(MODEL_READY_SQL)

        sitescore = connection.execute(
            """
            SELECT *
            FROM model_ready.candidate_site_view
            WHERE tenant_id = %s AND store_id = %s
            """,
            (tenant_a, target_a),
        ).fetchone()
        assert sitescore is not None
        columns = [
            column.name
            for column in connection.execute(
                "SELECT * FROM model_ready.candidate_site_view LIMIT 0"
            ).description
        ]
        site = dict(zip(columns, sitescore, strict=True))
        assert site["prior_90d_cell_net_revenue"] == pytest.approx(300.0)
        assert site["realized_90d_net_revenue"] == pytest.approx(900.0)
        assert site["prior_90d_cell_transaction_count"] == 1
        assert site["label_horizon_days"] == 90
        assert site["feature_cutoff_time"] == _at(ORIGIN, 0)
        assert site["label_maturity_time"] >= _at(ORIGIN + timedelta(days=90), 0)
        assert site["is_training_eligible"] is True
        assert site["exclusion_reason"] is None
        assert site["source_snapshot_ids"]

        heat = connection.execute(
            """
            SELECT *
            FROM model_ready.heatzone_training_view
            WHERE tenant_id = %s AND h3_index = %s AND origin_date = %s
            """,
            (tenant_a, H3_INDEX, ORIGIN),
        ).fetchone()
        assert heat is not None
        columns = [
            column.name
            for column in connection.execute(
                "SELECT * FROM model_ready.heatzone_training_view LIMIT 0"
            ).description
        ]
        zone = dict(zip(columns, heat, strict=True))
        assert zone["prior_90d_cell_net_revenue"] == pytest.approx(300.0)
        assert zone["realized_28d_cell_net_revenue"] == pytest.approx(900.0)
        assert zone["prior_opened_store_count"] == 1
        assert zone["label_horizon_days"] == 28
        assert zone["feature_cutoff_time"] == _at(ORIGIN, 0)
        assert zone["label_maturity_time"] >= _at(ORIGIN + timedelta(days=28), 0)
        assert zone["is_training_eligible"] is True
        assert zone["exclusion_reason"] is None
        assert zone["source_snapshot_ids"]

        contracts = dict(
            connection.execute(
                """
                SELECT relation_name, contract_state
                FROM model_ready.view_contracts
                WHERE relation_name IN (
                    'model_ready.candidate_site_view',
                    'model_ready.heatzone_training_view'
                )
                """
            ).fetchall()
        )
        assert contracts == {
            "model_ready.candidate_site_view": "ACTIVE",
            "model_ready.heatzone_training_view": "ACTIVE",
        }
