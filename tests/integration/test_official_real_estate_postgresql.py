from __future__ import annotations

import csv
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from modules.external_data.providers.taiwan_real_estate import (
    NTPC_HEADERS,
    SOURCES,
    DownloadedArtifact,
    OfficialRealEstateSourceError,
    parse_official_real_estate,
)
from product_ops.modeling.install_views import ModelReadyViewInstaller
from product_ops.modeling.real_estate_outcomes import OfficialRealEstateOutcomeStore
from shared.infrastructure.persistence.postgresql import PostgresEngine

pytestmark = pytest.mark.requires_live_env


def _urls(database: Any) -> tuple[str, str]:
    params = database.server.admin_params
    raw_host = (
        str(params["host"])
        if params.get("host") is not None
        else None
    )
    socket_query = (
        {"host": raw_host}
        if raw_host is not None and raw_host.startswith("/")
        else None
    )
    base = {
        "username": str(params.get("user") or "postgres"),
        "password": (
            str(params["password"])
            if params.get("password") is not None
            else None
        ),
        "host": None if socket_query else raw_host,
        "port": (
            int(params["port"])
            if params.get("port") is not None
            else None
        ),
        "database": database.dbname,
        "query": socket_query,
    }
    runtime = URL.create("postgresql", **base).render_as_string(
        hide_password=False
    )
    alembic = URL.create("postgresql+psycopg", **base).render_as_string(
        hide_password=False
    )
    return runtime, alembic


def _alembic_config(database: Any) -> Config:
    _runtime_url, alembic_url = _urls(database)
    config = Config("infra/db/migrations/alembic.ini")
    config.set_main_option("sqlalchemy.url", alembic_url.replace("%", "%%"))
    return config


def _upgrade_official_schema(database: Any) -> None:
    config = _alembic_config(database)
    command.stamp(config, "0002")
    command.upgrade(config, "head")


def _create_model_view_prerequisites(database: Any) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            CREATE SCHEMA core;
            CREATE SCHEMA data_plane;
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
                net_amount NUMERIC NOT NULL,
                currency TEXT NOT NULL,
                transaction_status TEXT NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL
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
                projected_at TIMESTAMPTZ NOT NULL
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


def _ntpc_csv(*, record_id: str, rows: tuple[dict[str, str], ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(NTPC_HEADERS)
    for overrides in rows:
        values = {header: "" for header in NTPC_HEADERS}
        values.update(
            {
                "district": "板橋區",
                "rps01": "房地(土地+建物)",
                "rps02": "新北市板橋區範例路２號",
                "rps03_area": "20",
                "rps07_yyymmddroc": "1140516",
                "rps08": "土地1建物1車位0",
                "rps11": "店面(店鋪)",
                "rps12": "商業用",
                "rps13": "鋼筋混凝土造",
                "rps15_area": "70",
                "rps16_quantity": "1",
                "rps17_quantity": "1",
                "rps18_quantity": "1",
                "rps19": "有",
                "rps20": "無",
                "rps21_amountsunitdollars": "16000000",
                "rps22_amountsunitdollars": "228571",
                "rps24_area": "0",
                "rps25_amountsunitdollars": "0",
                "rps27": record_id,
                "rps28_area": "65",
                "rps29_area": "0",
                "rps30_area": "5",
                "rps31": "無",
                "rps32": "",
            }
        )
        values.update(overrides)
        writer.writerow([values[column] for column in NTPC_HEADERS])
    return stream.getvalue().encode()


def _batch(
    *,
    record_id: str,
    rows: tuple[dict[str, str], ...],
    fetched_at: datetime,
    source_published_at: datetime,
) -> Any:
    content = _ntpc_csv(record_id=record_id, rows=rows)
    source = SOURCES["ntpc"]
    artifact = DownloadedArtifact(
        source=source,
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_type=source.media_type,
        fetched_at=fetched_at,
        final_url=source.source_url,
        source_published_at=source_published_at,
    )
    return parse_official_real_estate(artifact)


def test_alembic_head_installs_official_schema_and_both_view_branches(
    intake_blank_db: Any,
) -> None:
    runtime_url, _alembic_url = _urls(intake_blank_db)
    command.stamp(_alembic_config(intake_blank_db), "0002")
    _create_model_view_prerequisites(intake_blank_db)
    engine = PostgresEngine(
        runtime_url,
        bootstrap=False,
        validate_schema=False,
    )
    try:
        without_official = ModelReadyViewInstaller(engine).install()
        assert without_official["forecast"]["contract_state"] == "ACTIVE"
        assert (
            without_official["listing_property_avm"]["contract_state"]
            == "BLOCKED"
        )

        command.upgrade(_alembic_config(intake_blank_db), "head")
        with_official = ModelReadyViewInstaller(engine).install()
        assert with_official["forecast"]["contract_state"] == "ACTIVE"
        assert (
            with_official["listing_property_avm"]["contract_state"]
            == "ACTIVE"
        )
        with intake_blank_db.connect() as connection:
            current = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        assert current == ("0003",)
    finally:
        engine.close()


def test_snapshot_order_corrections_and_composite_lineage_fk(
    intake_blank_db: Any,
) -> None:
    _upgrade_official_schema(intake_blank_db)
    runtime_url, _alembic_url = _urls(intake_blank_db)
    engine = PostgresEngine(
        runtime_url,
        bootstrap=False,
        validate_schema=False,
    )
    store = OfficialRealEstateOutcomeStore(engine)
    try:
        initial = _batch(
            record_id="CORRECTION-001",
            rows=(
                {
                    "rps21_amountsunitdollars": "16000000",
                    "rps26": "initial",
                },
                {
                    "rps21_amountsunitdollars": "17000000",
                    "rps26": "authority correction",
                },
            ),
            fetched_at=datetime(2026, 7, 21, tzinfo=UTC),
            source_published_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
        initial_receipt = store.upsert(initial)
        assert initial_receipt["parsed_row_count"] == 2
        assert initial_receipt["projection_row_count"] == 1
        assert initial_receipt["observation_row_count"] == 2
        assert initial_receipt["inserted_row_count"] == 1

        newer = _batch(
            record_id="CORRECTION-001",
            rows=({"rps21_amountsunitdollars": "18000000"},),
            fetched_at=datetime(2026, 7, 23, tzinfo=UTC),
            source_published_at=datetime(2026, 7, 22, tzinfo=UTC),
        )
        assert store.upsert(newer)["updated_row_count"] == 1

        stale = _batch(
            record_id="CORRECTION-001",
            rows=({"rps21_amountsunitdollars": "15000000"},),
            fetched_at=datetime(2026, 7, 24, tzinfo=UTC),
            source_published_at=datetime(2026, 7, 21, tzinfo=UTC),
        )
        stale_receipt = store.upsert(stale)
        assert stale_receipt["stale_row_count"] == 1
        assert stale_receipt["updated_row_count"] == 0

        current = engine.query_one(
            "SELECT transaction_id, total_price_twd, last_seen_run_id, "
            "last_source_order_at FROM "
            "external_data.real_estate_transactions "
            "WHERE source_record_id = ?",
            ("CORRECTION-001",),
        )
        assert current is not None
        assert int(current["total_price_twd"]) == 18_000_000
        assert str(current["last_seen_run_id"]) == str(newer.run_id)
        assert datetime.fromisoformat(
            str(current["last_source_order_at"])
        ) == datetime(2026, 7, 22, tzinfo=UTC)
        observations = engine.query_one(
            "SELECT count(*) AS count FROM "
            "external_data.real_estate_transaction_observations "
            "WHERE transaction_id = ?",
            (current["transaction_id"],),
        )
        assert observations == {"count": 4}

        other = _batch(
            record_id="OTHER-002",
            rows=({"rps21_amountsunitdollars": "19000000"},),
            fetched_at=datetime(2026, 7, 25, tzinfo=UTC),
            source_published_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
        store.upsert(other)
        other_row = engine.query_one(
            "SELECT source_id, authority_partition, source_record_id, "
            "source_variant_id FROM external_data.real_estate_transactions "
            "WHERE source_record_id = ?",
            ("OTHER-002",),
        )
        assert other_row is not None
        psycopg = pytest.importorskip("psycopg")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with engine.transaction():
                engine.execute(
                    "INSERT INTO "
                    "external_data.real_estate_transaction_observations ("
                    "run_id, transaction_id, source_id, authority_partition, "
                    "source_record_id, source_variant_id, source_file, "
                    "source_row_number, raw_record_sha256, raw_fields, "
                    "observed_at, source_order_at, source_snapshot_id"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSONB), "
                    "?, ?, ?)",
                    (
                        other.run_id,
                        current["transaction_id"],
                        other_row["source_id"],
                        other_row["authority_partition"],
                        other_row["source_record_id"],
                        other_row["source_variant_id"],
                        "mismatch.csv",
                        999,
                        "f" * 64,
                        "{}",
                        datetime(2026, 7, 25, tzinfo=UTC),
                        datetime(2026, 7, 24, tzinfo=UTC),
                        other.source_snapshot_id,
                    ),
                )
    finally:
        engine.close()


def test_identity_correction_fails_closed_and_transfer_number_cannot_fork_sale(
    intake_blank_db: Any,
) -> None:
    _upgrade_official_schema(intake_blank_db)
    runtime_url, _alembic_url = _urls(intake_blank_db)
    engine = PostgresEngine(
        runtime_url,
        bootstrap=False,
        validate_schema=False,
    )
    store = OfficialRealEstateOutcomeStore(engine)
    try:
        initial = _batch(
            record_id="IDENTITY-001",
            rows=({"rps32": ""},),
            fetched_at=datetime(2026, 7, 21, tzinfo=UTC),
            source_published_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
        store.upsert(initial)

        transfer_number_added = _batch(
            record_id="IDENTITY-001",
            rows=({"rps32": "1"},),
            fetched_at=datetime(2026, 7, 23, tzinfo=UTC),
            source_published_at=datetime(2026, 7, 22, tzinfo=UTC),
        )
        receipt = store.upsert(transfer_number_added)
        assert receipt["updated_row_count"] == 1
        projection = engine.query_one(
            "SELECT count(*) AS count, max(transfer_number) AS transfer_number "
            "FROM external_data.real_estate_transactions "
            "WHERE source_record_id = ?",
            ("IDENTITY-001",),
        )
        assert projection == {"count": 1, "transfer_number": "1"}

        changed_identity = _batch(
            record_id="IDENTITY-001",
            rows=({"rps02": "新北市板橋區範例路２號３樓", "rps32": "1"},),
            fetched_at=datetime(2026, 7, 25, tzinfo=UTC),
            source_published_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
        with pytest.raises(
            OfficialRealEstateSourceError,
            match="identity fingerprint changed",
        ):
            store.upsert(changed_identity)

        persisted = engine.query_one(
            "SELECT address, last_seen_run_id, last_source_snapshot_id "
            "FROM external_data.real_estate_transactions "
            "WHERE source_record_id = ?",
            ("IDENTITY-001",),
        )
        assert persisted is not None
        assert persisted["address"] == "新北市板橋區範例路２號"
        assert str(persisted["last_seen_run_id"]) == str(
            transfer_number_added.run_id
        )
        assert (
            persisted["last_source_snapshot_id"]
            == transfer_number_added.source_snapshot_id
        )
        failed = engine.query_one(
            "SELECT status, error_code FROM "
            "external_data.real_estate_ingestion_runs WHERE run_id = ?",
            (changed_identity.run_id,),
        )
        assert failed == {
            "status": "FAILED",
            "error_code": "OfficialRealEstateSourceError",
        }
    finally:
        engine.close()


def test_concurrent_snapshot_replay_returns_one_stable_receipt(
    intake_blank_db: Any,
) -> None:
    _upgrade_official_schema(intake_blank_db)
    runtime_url, _alembic_url = _urls(intake_blank_db)
    engine = PostgresEngine(
        runtime_url,
        bootstrap=False,
        validate_schema=False,
        max_pool_size=4,
    )
    batch = _batch(
        record_id="CONCURRENT-001",
        rows=({"rps21_amountsunitdollars": "20000000"},),
        fetched_at=datetime(2026, 7, 26, tzinfo=UTC),
        source_published_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(
                executor.map(
                    lambda _index: OfficialRealEstateOutcomeStore(
                        engine
                    ).upsert(batch),
                    range(2),
                )
            )

        assert {receipt["status"] for receipt in receipts} == {
            "succeeded",
            "already_applied",
        }
        assert {
            (
                receipt["parsed_row_count"],
                receipt["projection_row_count"],
                receipt["observation_row_count"],
                receipt["inserted_row_count"],
                receipt["updated_row_count"],
                receipt["unchanged_row_count"],
                receipt["stale_row_count"],
            )
            for receipt in receipts
        } == {(1, 1, 1, 1, 0, 0, 0)}
        counts = engine.query_one(
            "SELECT "
            "(SELECT count(*) FROM "
            "external_data.real_estate_ingestion_runs) AS runs, "
            "(SELECT count(*) FROM "
            "external_data.real_estate_transactions) AS transactions, "
            "(SELECT count(*) FROM "
            "external_data.real_estate_transaction_observations) AS observations"
        )
        assert counts == {
            "runs": 1,
            "transactions": 1,
            "observations": 1,
        }
    finally:
        engine.close()


def test_concurrent_out_of_order_snapshots_keep_newest_projection(
    intake_blank_db: Any,
) -> None:
    _upgrade_official_schema(intake_blank_db)
    runtime_url, _alembic_url = _urls(intake_blank_db)
    engine = PostgresEngine(
        runtime_url,
        bootstrap=False,
        validate_schema=False,
        max_pool_size=4,
    )
    older = _batch(
        record_id="CONCURRENT-ORDER-001",
        rows=({"rps21_amountsunitdollars": "21000000"},),
        fetched_at=datetime(2026, 7, 25, tzinfo=UTC),
        source_published_at=datetime(2026, 7, 24, tzinfo=UTC),
    )
    newer = _batch(
        record_id="CONCURRENT-ORDER-001",
        rows=({"rps21_amountsunitdollars": "23000000"},),
        fetched_at=datetime(2026, 7, 27, tzinfo=UTC),
        source_published_at=datetime(2026, 7, 26, tzinfo=UTC),
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(
                executor.map(
                    lambda batch: OfficialRealEstateOutcomeStore(
                        engine
                    ).upsert(batch),
                    (older, newer),
                )
            )

        assert {receipt["status"] for receipt in receipts} == {"succeeded"}
        assert sum(
            int(receipt["inserted_row_count"]) for receipt in receipts
        ) == 1
        assert sum(
            int(receipt["updated_row_count"])
            + int(receipt["stale_row_count"])
            for receipt in receipts
        ) == 1
        current = engine.query_one(
            "SELECT total_price_twd, last_seen_run_id, "
            "last_source_snapshot_id, last_source_order_at FROM "
            "external_data.real_estate_transactions "
            "WHERE source_record_id = ?",
            ("CONCURRENT-ORDER-001",),
        )
        assert current is not None
        assert int(current["total_price_twd"]) == 23_000_000
        assert str(current["last_seen_run_id"]) == str(newer.run_id)
        assert current["last_source_snapshot_id"] == newer.source_snapshot_id
        assert datetime.fromisoformat(
            str(current["last_source_order_at"])
        ) == datetime(2026, 7, 26, tzinfo=UTC)
        observations = engine.query_one(
            "SELECT count(*) AS count FROM "
            "external_data.real_estate_transaction_observations"
        )
        assert observations == {"count": 2}
    finally:
        engine.close()
