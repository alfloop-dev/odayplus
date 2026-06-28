from __future__ import annotations

import json

import pytest

from apps.cli.oday_cli.ops import (
    OpsPlanError,
    build_backfill_plan,
    build_migration_plan,
    main,
)


def test_migration_plan_indexes_revision_hashes_and_rollback() -> None:
    plan = build_migration_plan(environment="dev")

    assert plan.environment == "dev"
    assert plan.database_url_env == "ODAY_DATABASE_URL"
    assert plan.target_revision == "head"
    assert plan.dry_run is True
    assert [step.revision for step in plan.steps] == ["0001"]
    assert len(plan.steps[0].sha256) == 64
    assert plan.rollback["command"] == "alembic downgrade -1"


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
