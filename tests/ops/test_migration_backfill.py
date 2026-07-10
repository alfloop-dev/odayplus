from __future__ import annotations

import json

import pytest

from apps.cli.oday_cli.ops import (
    OpsPlanError,
    build_backfill_plan,
    build_migration_plan,
    build_migration_run,
    main,
)


def test_migration_plan_indexes_revision_hashes_and_rollback() -> None:
    plan = build_migration_plan(environment="dev")

    assert plan.environment == "dev"
    assert plan.database_url_env == "ODAY_DATABASE_URL"
    assert plan.target_revision == "head"
    assert plan.dry_run is True
    assert [step.revision for step in plan.steps] == ["0001", "0002"]
    assert len(plan.manifest_sha256) == 64
    assert all(len(step.sha256) == 64 for step in plan.steps)
    assert all(any(asset.role == "sql" for asset in step.assets) for step in plan.steps)
    assert plan.rollback["command"] == "alembic downgrade -1"


def test_migration_plan_uses_explicit_alembic_sql_references() -> None:
    plan = build_migration_plan(environment="dev")
    data_domain_step = next(step for step in plan.steps if step.revision == "0002")
    asset_paths = {asset.path for asset in data_domain_step.assets}

    assert "infra/db/migrations/000002_data_domain_canonical_entities.sql" in asset_paths
    assert "infra/db/migrations/000002_durable_e2e_persistence.sql" not in asset_paths


def test_migration_runner_validates_manifest_without_executing() -> None:
    plan = build_migration_plan(environment="dev")
    run = build_migration_run(
        environment="dev",
        expected_manifest_sha256=plan.manifest_sha256,
    )

    assert run.dry_run is True
    assert run.returncode is None
    assert run.checksum_status == "verified"
    assert run.command == ("alembic", "-c", "infra/db/migrations/alembic.ini", "upgrade", "head")
    assert run.manifest_sha256 == plan.manifest_sha256


def test_migration_runner_rejects_manifest_mismatch() -> None:
    with pytest.raises(OpsPlanError, match="migration checksum mismatch"):
        build_migration_run(
            environment="dev",
            expected_manifest_sha256="0" * 64,
        )


def test_backfill_plan_is_idempotent_for_same_inputs() -> None:
    plan = build_backfill_plan(
        environment="staging",
        job_type="model-ready-backfill",
        source_snapshot_id="txn-20260627",
        target_view="forecast_training_view",
        window_start="2026-06-01T00:00:00Z",
        window_end="2026-06-28T00:00:00Z",
    )
    repeated = build_backfill_plan(
        environment="staging",
        job_type="model-ready-backfill",
        source_snapshot_id="txn-20260627",
        target_view="forecast_training_view",
        window_start="2026-06-01T00:00:00Z",
        window_end="2026-06-28T00:00:00Z",
    )

    assert plan.idempotency_key == repeated.idempotency_key
    assert plan.quarantine_table == "operations.quarantine_forecast_training_view"
    assert "point_in_time_boundaries" in plan.checks
    assert "data_quality_threshold" in plan.checks


def test_backfill_plan_rejects_empty_or_invalid_window() -> None:
    with pytest.raises(OpsPlanError, match="window_start"):
        build_backfill_plan(
            environment="dev",
            job_type="model-ready-backfill",
            source_snapshot_id="txn-20260627",
            target_view="forecast_training_view",
            window_start="2026-06-28T00:00:00Z",
            window_end="2026-06-01T00:00:00Z",
        )


def test_cli_writes_backfill_plan_json(tmp_path) -> None:
    output = tmp_path / "backfill-plan.json"

    exit_code = main(
        [
            "backfill-plan",
            "--environment",
            "dev",
            "--job-type",
            "model-ready-backfill",
            "--source-snapshot-id",
            "machine-20260627",
            "--target-view",
            "store_machine_timeseries_view",
            "--window-start",
            "2026-06-01T00:00:00Z",
            "--window-end",
            "2026-06-28T00:00:00Z",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["environment"] == "dev"
    assert payload["idempotency_key"].startswith("backfill:")
    assert payload["target_view"] == "store_machine_timeseries_view"
