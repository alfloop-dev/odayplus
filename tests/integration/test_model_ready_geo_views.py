from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

from models.shared_ml.oss_estimators import (
    load_estimator_artifact,
    train_oss_estimator,
)
from modules.heatzone.domain import (
    score_heatzones_from_model_predictions,
    to_heatzone_model_row,
)
from modules.sitescore.domain import (
    RevenuePredictionBand,
    score_sites_from_model_predictions,
    to_sitescore_model_row,
)
from scripts.models.contracts import MODEL_SPECS, DataBounds
from scripts.models.install_views import (
    ELIGIBILITY_PREREQUISITE_SQL,
    ModelReadyViewInstaller,
    ModelReadyViewInstallError,
)
from scripts.models.release import prepare_model_rows
from scripts.models.storage import LoadedModelReadyRows

MODEL_READY_SQL = (
    Path(__file__).parents[2] / "scripts/models/sql/model_ready_views.sql"
).read_text(encoding="utf-8")
NAMESPACE = UUID("f58c8d6c-baa1-4e58-9984-383fb725cd8a")
ORIGIN = date(2025, 4, 1)
H3_INDEX = "89263064c2fffff"
FUTURE_H3_INDEX = "89263064d37ffff"

pytestmark = pytest.mark.requires_live_env


def _id(value: str) -> UUID:
    return uuid5(NAMESPACE, value)


def _at(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour=hour), tzinfo=UTC)


class _PsycopgInstallationClient:
    def __init__(self, connection) -> None:
        self.connection = connection

    @contextmanager
    def transaction(self):
        with self.connection.transaction():
            yield self

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.connection.execute(sql.replace("?", "%s"), params)

    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        cursor = self.connection.execute(sql.replace("?", "%s"), params)
        columns = [column.name for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        cursor = self.connection.execute(sql.replace("?", "%s"), params)
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(
            zip(
                [column.name for column in cursor.description],
                row,
                strict=True,
            )
        )


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
            processed_count BIGINT NOT NULL,
            valid_loaded BIGINT NOT NULL,
            quarantined_count BIGINT NOT NULL,
            reconciled BOOLEAN NOT NULL,
            partition_complete BOOLEAN NOT NULL,
            finished_at TIMESTAMPTZ
        );

        CREATE TABLE data_plane.canonical_lineage (
            source_snapshot_id UUID NOT NULL,
            run_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            canonical_table TEXT NOT NULL,
            canonical_id UUID NOT NULL,
            projected_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (source_snapshot_id, canonical_table, canonical_id)
        );

        CREATE TABLE data_plane.transaction_authority (
            transaction_id UUID PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_snapshot_id UUID NOT NULL
        );

        CREATE TABLE data_plane.place_geography (
            source_snapshot_id UUID PRIMARY KEY,
            source_id TEXT NOT NULL,
            tenant_id UUID NOT NULL,
            store_id UUID NOT NULL,
            city TEXT,
            district TEXT,
            latitude NUMERIC,
            longitude NUMERIC,
            geocode_confidence NUMERIC,
            h3_res_9 TEXT,
            run_id UUID NOT NULL,
            valid_from TIMESTAMPTZ NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL
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
            run_id, source_kind, partition_key, status,
            processed_count, valid_loaded, quarantined_count,
            reconciled, partition_complete, finished_at
        ) VALUES (%s, %s, %s, 'SUCCEEDED', 0, 0, 0, TRUE, TRUE, %s)
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
    h3_index: str = H3_INDEX,
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
        (address_id, h3_index),
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
    _insert_lineage(
        connection,
        name=f"{tenant}:{store}:core.stores",
        run_id=identity_run,
        tenant_id=tenant_id,
        table="core.stores",
        entity_id=store_id,
        projected_at=_at(date(2024, 1, 1), 11),
    )
    _insert_geography(
        connection,
        tenant_id=tenant_id,
        store_id=store_id,
        name=f"{tenant}:{store}:initial",
        run_id=identity_run,
        h3_index=h3_index,
        valid_from=_at(date(2024, 1, 1), 8),
        observed_at=_at(date(2024, 1, 1), 10),
    )
    return tenant_id, store_id, address_id


def _insert_geography(
    connection,
    *,
    tenant_id: UUID,
    store_id: UUID,
    name: str,
    run_id: UUID,
    h3_index: str,
    valid_from: datetime,
    observed_at: datetime,
) -> UUID:
    snapshot_id = _id(f"geography:{name}")
    connection.execute(
        """
        INSERT INTO data_plane.place_geography (
            source_snapshot_id, source_id, tenant_id, store_id,
            city, district, latitude, longitude, geocode_confidence,
            h3_res_9, run_id, valid_from, observed_at
        ) VALUES (
            %s, %s, %s, %s, 'Taipei', 'Xinyi', 25.033, 121.565,
            0.98, %s, %s, %s, %s
        )
        ON CONFLICT (source_snapshot_id) DO NOTHING
        """,
        (
            snapshot_id,
            name,
            tenant_id,
            store_id,
            h3_index,
            run_id,
            valid_from,
            observed_at,
        ),
    )
    return snapshot_id


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
    connection.execute(
        """
        INSERT INTO data_plane.transaction_authority (
            transaction_id, source_kind, source_snapshot_id
        ) VALUES (%s, 'orders', %s)
        """,
        (transaction_id, _id(f"snapshot:{name}")),
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


def _seed_point_in_time_history(
    connection,
) -> tuple[UUID, UUID, UUID, UUID, dict[date, UUID]]:
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

    tenant_c, prior_c, _ = _insert_store(
        connection,
        tenant="c",
        store="prior",
        opened_on=date(2024, 1, 1),
    )
    _, target_c, _ = _insert_store(
        connection,
        tenant="c",
        store="target",
        opened_on=ORIGIN,
    )
    _insert_transaction(
        connection,
        tenant_id=tenant_c,
        store_id=prior_c,
        name="c-prior",
        event_time=_at(date(2025, 3, 1), 10),
        amount=500.0,
        daily_runs=daily_runs,
    )

    future_geo_run = _insert_run(
        connection,
        name="place:a:future-move",
        source_kind="place",
        partition_key="2025-05-01",
        finished_at=_at(date(2025, 5, 1), 12),
    )
    _insert_geography(
        connection,
        tenant_id=tenant_a,
        store_id=prior_a,
        name="a:prior:future-move",
        run_id=future_geo_run,
        h3_index=FUTURE_H3_INDEX,
        valid_from=_at(date(2025, 5, 1), 8),
        observed_at=_at(date(2025, 5, 1), 10),
    )
    connection.execute(
        """
        UPDATE core.address_locations
        SET h3_res_9 = %s
        WHERE address_id = (
            SELECT address_id FROM core.stores WHERE store_id = %s
        )
        """,
        (FUTURE_H3_INDEX, prior_a),
    )
    return tenant_a, target_a, tenant_c, target_c, daily_runs


def _rows_as_dicts(connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, params)
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _prepare_train_reload(
    model_key: str,
    rows: list[dict[str, Any]],
):
    spec = MODEL_SPECS[model_key]
    loaded_rows = LoadedModelReadyRows(
        rows=tuple(rows),
        relation=spec.relation,
        bounds=DataBounds(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            max(2, len(rows)),
        ),
        query_sha256=model_key[0] * 64,
        as_of_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    prepared = prepare_model_rows(spec, loaded_rows)
    features = [row.mapping["features"] for row in prepared]
    labels = [float(row.mapping["labels"][spec.label_name]) for row in prepared]
    trained = train_oss_estimator(
        algorithm=spec.algorithm,
        feature_rows=features,
        labels=labels,
        feature_names=spec.feature_columns,
    )
    artifact = trained.estimator.to_artifact_bytes()
    reloaded = load_estimator_artifact(artifact)
    before = trained.estimator.predict(features)
    after = reloaded.predict(features)
    assert after == pytest.approx(before)
    return spec, prepared, reloaded, after


def test_postgresql_views_compute_real_causal_labels_and_isolate_tenants(
    intake_blank_db,
) -> None:
    with intake_blank_db.connect() as connection:
        _install_minimal_authoritative_schema(connection)
        tenant_a, target_a, tenant_c, target_c, daily_runs = _seed_point_in_time_history(connection)
        prerequisite_cursor = connection.execute(ELIGIBILITY_PREREQUISITE_SQL)
        prerequisite_columns = [column.name for column in prerequisite_cursor.description]
        prerequisite_counts = dict(
            zip(
                prerequisite_columns,
                prerequisite_cursor.fetchone(),
                strict=True,
            )
        )
        assert prerequisite_counts["total_store_rows"] == 6
        assert prerequisite_counts["stores_missing_opened_on"] == 0
        assert prerequisite_counts["sitescore_anchor_prerequisite_rows"] == 6
        assert prerequisite_counts["heatzone_cell_prerequisite_rows"] == 4
        assert prerequisite_counts["successful_twd_transaction_rows"] == 5
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
        assert (
            site["feature_snapshot_time"]
            < site["prediction_origin_time"]
            < site["label_maturity_time"]
        )
        for provenance_column in (
            "feature_identity_available_at",
            "feature_transaction_available_at",
            "feature_partition_available_at",
        ):
            assert site[provenance_column] < site["prediction_origin_time"]
        assert site["is_training_eligible"] is True
        assert site["exclusion_reason"] is None
        assert site["source_snapshot_ids"]
        assert site["h3_index"] == H3_INDEX

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
        assert (
            zone["feature_snapshot_time"]
            < zone["prediction_origin_time"]
            < zone["label_maturity_time"]
        )
        for provenance_column in (
            "feature_identity_available_at",
            "feature_transaction_available_at",
            "feature_partition_available_at",
        ):
            assert zone[provenance_column] < zone["prediction_origin_time"]
        assert zone["is_training_eligible"] is True
        assert zone["exclusion_reason"] is None
        assert zone["source_snapshot_ids"]

        zero_site = connection.execute(
            """
            SELECT realized_90d_net_revenue, label_transaction_count,
                   is_training_eligible, exclusion_reason
            FROM model_ready.candidate_site_view
            WHERE tenant_id = %s AND store_id = %s
            """,
            (tenant_c, target_c),
        ).fetchone()
        assert zero_site == (0.0, 0, True, None)

        zero_zone = connection.execute(
            """
            SELECT realized_28d_cell_net_revenue, label_transaction_count,
                   is_training_eligible, exclusion_reason
            FROM model_ready.heatzone_training_view
            WHERE tenant_id = %s AND h3_index = %s AND origin_date = %s
            """,
            (tenant_c, H3_INDEX, ORIGIN),
        ).fetchone()
        assert zero_zone == (0.0, 0, True, None)

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

        sitescore_rows = _rows_as_dicts(
            connection,
            """
            SELECT *
            FROM model_ready.candidate_site_view
            WHERE opened_on = %s AND is_training_eligible
            ORDER BY tenant_id
            """,
            (ORIGIN,),
        )
        site_spec, site_prepared, site_estimator, site_predictions = _prepare_train_reload(
            "sitescore", sitescore_rows
        )
        site_runtime_rows = [
            to_sitescore_model_row(
                {
                    **source,
                    "candidate_site_id": str(source["store_id"]),
                }
            )
            for source in sitescore_rows
        ]
        assert site_estimator.predict(site_runtime_rows) == pytest.approx(site_predictions)
        site_reports = score_sites_from_model_predictions(
            [
                {
                    **source,
                    "candidate_site_id": str(source["store_id"]),
                }
                for source in sitescore_rows
            ],
            [
                RevenuePredictionBand(
                    p10=max(0.0, value * 0.9),
                    p50=max(0.0, value),
                    p90=max(0.0, value * 1.1),
                )
                for value in site_predictions
            ],
            model_version="sitescore:pg-e2e",
            output_transform=site_spec.output_transform,
        )
        assert len(site_prepared) == len(site_reports) == 3
        assert site_reports[0].m12.p50 == pytest.approx(
            max(0.0, site_predictions[0]) * 30.4375 / 90.0,
            abs=0.01,
        )

        heatzone_rows = _rows_as_dicts(
            connection,
            """
            SELECT *
            FROM model_ready.heatzone_training_view
            WHERE origin_date = %s AND is_training_eligible
            ORDER BY tenant_id
            """,
            (ORIGIN,),
        )
        heat_spec, heat_prepared, heat_estimator, heat_predictions = _prepare_train_reload(
            "heatzone", heatzone_rows
        )
        heat_runtime_rows = [to_heatzone_model_row(source) for source in heatzone_rows]
        assert heat_estimator.predict(heat_runtime_rows) == pytest.approx(heat_predictions)
        heat_reports = score_heatzones_from_model_predictions(
            heatzone_rows,
            heat_predictions,
            model_version="heatzone:pg-e2e",
            output_transform=heat_spec.output_transform,
        )
        assert len(heat_prepared) == len(heat_reports) == 3
        assert all(0.0 <= report.score <= 100.0 for report in heat_reports)
        assert {report.model_version for report in heat_reports} == {"heatzone:pg-e2e"}

        incomplete_day = ORIGIN + timedelta(days=2)
        connection.execute(
            """
            UPDATE data_plane.ingestion_runs
            SET partition_complete = FALSE
            WHERE run_id = %s
            """,
            (daily_runs[incomplete_day],),
        )
        _insert_run(
            connection,
            name="trade-does-not-fill-orders-coverage",
            source_kind="trade",
            partition_key=incomplete_day.isoformat(),
            finished_at=_at(incomplete_day, 23),
        )
        assert connection.execute(
            """
            SELECT is_training_eligible, exclusion_reason
            FROM model_ready.candidate_site_view
            WHERE tenant_id = %s AND store_id = %s
            """,
            (tenant_a, target_a),
        ).fetchone() == (
            False,
            "LABEL_90D_PARTITION_COVERAGE_INCOMPLETE",
        )

        for invalid_update in (
            "UPDATE data_plane.ingestion_runs SET partition_complete = FALSE WHERE run_id = %s",
            "UPDATE data_plane.ingestion_runs SET reconciled = FALSE WHERE run_id = %s",
            "UPDATE data_plane.ingestion_runs "
            "SET processed_count = 1, valid_loaded = 0 WHERE run_id = %s",
            "UPDATE data_plane.ingestion_runs SET quarantined_count = 1 WHERE run_id = %s",
        ):
            connection.execute(
                """
                UPDATE data_plane.ingestion_runs
                SET partition_complete = TRUE,
                    reconciled = TRUE,
                    processed_count = 0,
                    valid_loaded = 0,
                    quarantined_count = 0
                WHERE run_id = %s
                """,
                (daily_runs[incomplete_day],),
            )
            connection.execute(
                invalid_update,
                (daily_runs[incomplete_day],),
            )
            eligible, exclusion = connection.execute(
                """
                SELECT is_training_eligible, exclusion_reason
                FROM model_ready.candidate_site_view
                WHERE tenant_id = %s AND store_id = %s
                """,
                (tenant_a, target_a),
            ).fetchone()
            assert eligible is False
            assert exclusion == "LABEL_90D_PARTITION_COVERAGE_INCOMPLETE"


def test_postgresql_install_validation_failure_rolls_back_ddl_and_registry(
    intake_blank_db,
    tmp_path: Path,
) -> None:
    registry_entry = (
        "        'model_ready.heatzone_training_view',\n"
        "        'heatzone_training_view',\n"
        "        'heatzone-training-view-v2',"
    )
    assert MODEL_READY_SQL.count(registry_entry) == 1
    invalid_sql = (
        "CREATE TABLE public.model_ready_atomic_probe (value INTEGER NOT NULL);\n"
        "INSERT INTO public.model_ready_atomic_probe VALUES (1);\n"
        + MODEL_READY_SQL.replace(
            registry_entry,
            registry_entry.replace(
                "'heatzone-training-view-v2'",
                "'heatzone-training-view-invalid'",
            ),
        )
    )
    sql_path = tmp_path / "invalid_model_ready_views.sql"
    sql_path.write_text(invalid_sql, encoding="utf-8")

    with intake_blank_db.connect() as connection:
        _install_minimal_authoritative_schema(connection)
        installer = ModelReadyViewInstaller(
            _PsycopgInstallationClient(connection),
            sql_path=sql_path,
        )

        with pytest.raises(ModelReadyViewInstallError, match="version mismatch"):
            installer.install()

        assert connection.execute(
            "SELECT to_regclass('public.model_ready_atomic_probe')"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT to_regclass('model_ready.view_contracts')"
        ).fetchone() == (None,)
