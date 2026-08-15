from __future__ import annotations

from pathlib import Path

MIGRATION_SQL = Path("infra/db/migrations/000002_data_domain_canonical_entities.sql")


def test_data_domain_migration_declares_required_canonical_entities() -> None:
    sql = MIGRATION_SQL.read_text(encoding="utf-8")

    required_tables = (
        "core.stores",
        "core.machines",
        "core.transactions",
        "core.machine_cycles",
        "geo.h3_cells",
        "geo.pois",
        "geo.competitor_stores",
        "expansion.listings",
        "expansion.candidate_sites",
        "learning.model_versions",
        "learning.prediction_runs",
        "learning.predictions",
        "operations.forecast_outputs",
        "asset.valuation_runs",
        "network.network_plans",
        "audit.data_snapshots",
    )

    for table in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_data_domain_migration_has_fk_indexes_and_time_fields() -> None:
    sql = MIGRATION_SQL.read_text(encoding="utf-8")

    required_fragments = (
        "store_id UUID NOT NULL REFERENCES core.stores(store_id)",
        "machine_id UUID REFERENCES core.machines(machine_id)",
        "prediction_run_id UUID NOT NULL REFERENCES learning.prediction_runs(prediction_run_id)",
        "snapshot_id UUID REFERENCES audit.data_snapshots(snapshot_id)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_store_time",
        "CREATE INDEX IF NOT EXISTS idx_machine_cycles_machine_time",
        "CREATE INDEX IF NOT EXISTS idx_prediction_runs_model_time",
        "CREATE INDEX IF NOT EXISTS idx_forecast_outputs_run",
        "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP",
    )

    for fragment in required_fragments:
        assert fragment in sql
