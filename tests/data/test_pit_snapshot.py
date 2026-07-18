from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from modules.learninghub.domain.dataset_snapshot import (
    PointInTimeViolation,
    build_dataset_snapshot,
    validate_point_in_time,
)


def test_model_ready_dbt_baseline_views_are_versioned() -> None:
    model_dir = Path("pipelines/dbt/models/model_ready")
    expected_views = {
        "geo_grid_view",
        "candidate_site_view",
        "store_machine_timeseries_view",
        "forecast_training_view",
        "intervention_panel_view",
        "valuation_view",
        "network_plan_view",
        "brand_transfer_view",
        "ramp_curve_view",
        "matched_control_view",
    }

    for view_name in expected_views:
        sql = (model_dir / f"{view_name}.sql").read_text(encoding="utf-8")
        assert f"'{view_name}' as view_name" in sql
        assert "'v1' as view_version" in sql
        assert "feature_snapshot_time" in sql
        assert "prediction_origin_time" in sql
        assert "source_snapshot_ids" in sql
        assert "is_training_eligible" in sql
        assert "is_scoring_eligible" in sql


def test_dataset_snapshot_indexes_view_versions_sources_and_entity_count() -> None:
    snapshot = build_dataset_snapshot(
        [
            {
                "view_name": "forecast_training_view",
                "view_version": "v1",
                "entity_id": "store-1",
                "feature_snapshot_time": "2026-06-27T00:00:00Z",
                "prediction_origin_time": "2026-06-27T00:00:00Z",
                "source_snapshot_ids": ["txn-20260626", "machine-20260626"],
                "data_quality_score": 0.94,
                "revenue_lag_7": 1400.0,
            },
            {
                "view_name": "forecast_training_view",
                "view_version": "v1",
                "entity_id": "store-2",
                "feature_snapshot_time": datetime(2026, 6, 27, tzinfo=UTC),
                "prediction_origin_time": datetime(2026, 6, 27, tzinfo=UTC),
                "source_snapshot_ids": ["txn-20260626"],
                "is_training_eligible": False,
                "is_scoring_eligible": True,
                "exclusion_reason": "label_not_mature",
            },
        ],
        dataset_snapshot_id="ds-forecast-20260627",
    )

    assert snapshot.dataset_snapshot_id == "ds-forecast-20260627"
    assert snapshot.view_versions == {"forecast_training_view": "v1"}
    assert snapshot.entity_count == 2
    assert snapshot.training_record_count == 1
    assert snapshot.scoring_record_count == 2
    assert snapshot.source_snapshot_ids == ("machine-20260626", "txn-20260626")


def test_point_in_time_validation_rejects_future_feature_snapshot() -> None:
    issues = validate_point_in_time(
        [
            {
                "view_name": "candidate_site_view",
                "view_version": "v1",
                "entity_id": "site-1",
                "feature_snapshot_time": "2026-06-28T00:00:00Z",
                "prediction_origin_time": "2026-06-27T00:00:00Z",
                "source_snapshot_ids": ["listing-20260628"],
            }
        ]
    )

    assert [issue.check_name for issue in issues] == ["feature_snapshot_after_prediction_origin"]


def test_build_dataset_snapshot_blocks_unmatured_training_label() -> None:
    with pytest.raises(PointInTimeViolation, match="label_maturity_time"):
        build_dataset_snapshot(
            [
                {
                    "view_name": "forecast_training_view",
                    "view_version": "v1",
                    "entity_id": "store-1",
                    "feature_snapshot_time": "2026-06-27T00:00:00Z",
                    "prediction_origin_time": "2026-06-27T00:00:00Z",
                    "source_snapshot_ids": ["txn-20260626"],
                    "labels": {"daily_net_revenue": 1800.0},
                    "label_maturity_time": "2026-06-28T00:00:00Z",
                }
            ]
        )


def test_point_in_time_validation_rejects_late_available_feature() -> None:
    with pytest.raises(PointInTimeViolation, match="available_from"):
        build_dataset_snapshot(
            [
                {
                    "view_name": "candidate_site_view",
                    "view_version": "v1",
                    "entity_id": "site-1",
                    "feature_snapshot_time": "2026-06-27T00:00:00Z",
                    "prediction_origin_time": "2026-06-27T12:00:00Z",
                    "source_snapshot_ids": ["competitor-20260628"],
                    "features": {
                        "available_from": "2026-06-28T00:00:00Z",
                        "event_time": "2026-06-26T00:00:00Z",
                        "observation_time": "2026-06-26T01:00:00Z",
                    },
                }
            ]
        )
