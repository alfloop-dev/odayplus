from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from models.shared_ml.production_contracts import (
    PRODUCTION_MODEL_CONTRACTS,
    production_model_names,
)
from scripts.models.contracts import MODEL_SPECS
from scripts.models.install_views import MODEL_READY_SQL_PATH, PREREQUISITE_COLUMNS
from scripts.models.real_estate_outcomes import MIGRATION_PATH


def test_official_outcome_migration_has_bounded_source_and_provenance_contracts() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    normalized = " ".join(sql.split())

    for fragment in (
        "CREATE TABLE IF NOT EXISTS external_data.real_estate_ingestion_runs",
        "CREATE TABLE IF NOT EXISTS external_data.real_estate_transactions",
        "CREATE TABLE IF NOT EXISTS external_data.real_estate_transaction_observations",
        "dataset_id = '25119'",
        "dataset_id = 'acce802d-58cc-4dff-9e7a-9ecc517f78be'",
        "license_id = 'government-open-data-license-v1'",
        "content_length_bytes <= 104857600",
        "parsed_row_count <= 250000",
        "content_sha256 ~ '^[0-9a-f]{64}$'",
        "schema_sha256 ~ '^[0-9a-f]{64}$'",
        "raw_record_sha256 ~ '^[0-9a-f]{64}$'",
        "raw_fields JSONB NOT NULL",
        "completion_year INTEGER",
        "completion_month INTEGER",
        "authority_partition TEXT NOT NULL",
        "source_variant_id TEXT NOT NULL",
        "identity_fingerprint TEXT NOT NULL",
        "last_source_order_at TIMESTAMPTZ NOT NULL",
        "projection_row_count INTEGER NOT NULL",
        "stale_row_count INTEGER NOT NULL",
    ):
        assert fragment in sql
    assert (
        "UNIQUE ( source_id, authority_partition, source_record_id, "
        "source_variant_id )" in normalized
    )
    assert (
        "FOREIGN KEY ( transaction_id, source_id, authority_partition, source_record_id, "
        "source_variant_id )" in normalized
    )
    assert (
        "UNIQUE ( transaction_id, source_id, authority_partition, "
        "source_record_id, source_variant_id )" in normalized
    )
    assert (
        "PRIMARY KEY ( run_id, transaction_id, source_file, source_row_number )"
        in normalized
    )


def test_listing_property_view_uses_only_supported_official_sale_fields() -> None:
    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8")
    lowered = sql.lower()
    valuation = lowered.split(
        "create or replace view model_ready.listing_property_valuation_view as",
        1,
    )[1].split("insert into model_ready.view_contracts", 1)[0]
    normalized_valuation = " ".join(valuation.split())

    assert "external_data.real_estate_transactions as outcome" in valuation
    assert "external_data.real_estate_ingestion_runs as ingestion" in valuation
    assert (
        "partition by outcome.source_id, outcome.authority_partition, "
        "outcome.source_record_id, outcome.source_variant_id" in normalized_valuation
    )
    assert "authority_partition" in valuation
    assert "source_variant_id" in valuation
    assert "source_record_id, ':', source_variant_id" in normalized_valuation
    assert "total_price_twd::double precision as realized_transaction_price" in valuation
    assert "government-open-data-license-v1" in valuation
    assert "source_snapshot_id" in valuation
    assert "transaction_date <= (fetched_at at time zone 'asia/taipei')::date" in valuation
    assert (
        "ranked.completion_year <= extract(year from ranked.transaction_date)::integer"
        in normalized_valuation
    )
    assert "unit_price_twd_sqm" not in valuation
    assert "rent_amount" not in valuation
    assert "gm_fwd" not in valuation
    assert "generate_series(" not in valuation
    assert "random(" not in valuation


def test_official_sales_do_not_bind_to_dealroom_avm_runtime_contract() -> None:
    dealroom = MODEL_SPECS["avm"]
    listing_property = MODEL_SPECS["listing_property_avm"]

    assert PRODUCTION_MODEL_CONTRACTS["avm"].model_name == "dealroom_avm"
    assert PRODUCTION_MODEL_CONTRACTS["avm"].training_spec_key == "avm"
    assert "listing_property_avm" not in PRODUCTION_MODEL_CONTRACTS
    assert "listing_property_avm" not in production_model_names().values()
    assert dealroom.model_name == "dealroom_avm"
    assert dealroom.relation == "model_ready.valuation_view"
    assert dealroom.feature_columns == (
        "tenant_id",
        "store_id",
        "gm_ttm",
        "gm_fwd_p10",
        "gm_fwd_p50",
        "gm_fwd_p90",
        "asset_book_value",
        "lease_remaining_months",
        "rent_amount",
        "forecast_confidence",
        "comparable_count",
    )

    assert listing_property.model_name == "listing_property_avm"
    assert listing_property.relation == "model_ready.listing_property_valuation_view"
    assert listing_property.label_column == "realized_transaction_price"
    assert listing_property.label_maturity_column == "label_maturity_time"
    assert listing_property.segment_column == "market_segment"
    assert listing_property.scope_columns == (
        "source_id",
        "authority_partition",
        "source_variant_id",
        "municipality",
        "district",
    )
    assert "unit_price_twd_sqm" not in listing_property.feature_columns
    assert "building_age_years" in listing_property.feature_columns
    assert "completion_year_known" in listing_property.feature_columns

    sql = MODEL_READY_SQL_PATH.read_text(encoding="utf-8").lower()
    assert "create or replace view model_ready.valuation_view" not in sql
    assert "mature_realized_transaction_outcome_relation_missing" in sql
    assert "create or replace view model_ready.avm_liquidity_training_view" not in sql
    assert "official_sale_outcome_has_no_marketing_interval" in sql
    assert (
        "external_data.real_estate_transactions" in PREREQUISITE_COLUMNS
        and "external_data.real_estate_ingestion_runs" in PREREQUISITE_COLUMNS
    )


def test_migration_path_is_versioned_and_committed_under_infra_db() -> None:
    assert (
        MIGRATION_PATH
        == Path("infra/db/migrations/000009_official_real_estate_outcomes.sql").resolve()
    )
    config = Config("infra/db/migrations/alembic.ini")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0003"]
    revision = script.get_revision("0003")
    assert revision is not None
    assert revision.down_revision == "0002"
