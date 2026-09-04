from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from modules.learninghub.domain.dataset_snapshot import (
    DatasetQualityAdmissionError,
    DatasetSnapshotError,
    ModelReadyRecord,
    PointInTimeViolation,
    build_dataset_snapshot,
    model_ready_record_from_mapping,
    validate_point_in_time,
    validate_quality_admission,
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


def test_model_ready_dbt_views_confidence_nullability() -> None:
    model_dir = Path("pipelines/dbt/models/model_ready")

    candidate_sql = (model_dir / "candidate_site_view.sql").read_text(encoding="utf-8")
    assert (
        "then least(listings.confidence, address_locations.geocode_confidence)"
        in candidate_sql
    )
    assert "else null" in candidate_sql
    assert "as confidence" in candidate_sql
    assert "coalesce(listings.confidence, 1.0)" not in candidate_sql
    assert "coalesce(address_locations.geocode_confidence, 1.0)" not in candidate_sql

    geo_sql = (model_dir / "geo_grid_view.sql").read_text(encoding="utf-8")
    assert (
        "then least(poi_counts.poi_confidence, competitor_counts.competitor_confidence)"
        in geo_sql
    )
    assert "else null" in geo_sql
    assert "as confidence" in geo_sql
    assert "coalesce(poi_counts.poi_confidence, 1.0)" not in geo_sql
    assert "coalesce(competitor_counts.competitor_confidence, 1.0)" not in geo_sql


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
                "confidence": 0.90,
                "revenue_lag_7": 1400.0,
            },
            {
                "view_name": "forecast_training_view",
                "view_version": "v1",
                "entity_id": "store-2",
                "feature_snapshot_time": datetime(2026, 6, 27, tzinfo=UTC),
                "prediction_origin_time": datetime(2026, 6, 27, tzinfo=UTC),
                "source_snapshot_ids": ["txn-20260626"],
                "data_quality_score": 0.90,
                "confidence": 0.85,
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
                "data_quality_score": 1.0,
                "confidence": 1.0,
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
                    "data_quality_score": 1.0,
                    "confidence": 1.0,
                    "labels": {"daily_net_revenue": 1800.0},
                    "label_maturity_time": "2026-06-28T00:00:00Z",
                }
            ]
        )


def _horizon_forecast_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "view_name": "forecast_training_view",
        "view_version": "v2",
        "entity_id": "tenant-1:store-1:2026-06-27:w4",
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:01Z",
        "source_snapshot_ids": ["txn-20260626", "txn-20260627"],
        "data_quality_score": 1.0,
        "confidence": 1.0,
        "labels": {"horizon_average_daily_net_revenue": 1800.0},
        "label_maturity_time": "2026-07-25T00:00:00Z",
        "training_as_of_time": "2026-07-26T00:00:00Z",
        "features": {"horizon_weeks": 4, "revenue_lag_1": 1700.0},
    }
    row.update(overrides)
    return row


def test_future_label_maturity_allowed_with_horizon_and_cutoff_evidence() -> None:
    snapshot = build_dataset_snapshot([_horizon_forecast_row()])

    record = snapshot.records[0]
    assert record.label_maturity_time is not None
    assert record.label_maturity_time > record.feature_snapshot_time
    assert record.training_as_of_time == datetime(2026, 7, 26, tzinfo=UTC)
    assert "training_as_of_time" not in record.features


def test_future_label_maturity_rejected_outside_horizon_window() -> None:
    with pytest.raises(PointInTimeViolation, match="horizon observation window"):
        build_dataset_snapshot(
            [
                _horizon_forecast_row(
                    label_maturity_time="2026-07-26T00:00:02Z",
                    training_as_of_time="2026-07-27T00:00:00Z",
                )
            ]
        )


def test_future_label_maturity_rejected_without_training_cutoff_evidence() -> None:
    with pytest.raises(PointInTimeViolation, match="training_as_of_time cutoff evidence"):
        build_dataset_snapshot([_horizon_forecast_row(training_as_of_time=None)])


def test_future_label_maturity_rejected_after_training_cutoff() -> None:
    with pytest.raises(PointInTimeViolation, match="must not be after training_as_of_time"):
        build_dataset_snapshot(
            [
                _horizon_forecast_row(
                    label_maturity_time="2026-07-25T00:00:00Z",
                    training_as_of_time="2026-07-24T00:00:00Z",
                )
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
                    "data_quality_score": 1.0,
                    "confidence": 1.0,
                    "features": {
                        "available_from": "2026-06-28T00:00:00Z",
                        "event_time": "2026-06-26T00:00:00Z",
                        "observation_time": "2026-06-26T01:00:00Z",
                    },
                }
            ]
        )


def test_model_ready_record_dataclass_defaults_to_none() -> None:
    record = ModelReadyRecord(
        view_name="forecast_training_view",
        view_version="v1",
        entity_id="store-100",
        feature_snapshot_time=datetime(2026, 6, 27, tzinfo=UTC),
        prediction_origin_time=datetime(2026, 6, 27, tzinfo=UTC),
    )
    assert record.data_quality_score is None
    assert record.confidence is None


def test_model_ready_record_from_mapping_nullable_no_fallback() -> None:
    # 1. Missing keys do NOT become 1.0
    row_missing: dict[str, object] = {
        "view_name": "forecast_training_view",
        "view_version": "v1",
        "entity_id": "store-101",
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:00Z",
    }
    record_missing = model_ready_record_from_mapping(row_missing)
    assert record_missing.data_quality_score is None
    assert record_missing.confidence is None

    # 2. Explicit None or empty string does NOT become 1.0
    row_none: dict[str, object] = {
        "view_name": "forecast_training_view",
        "view_version": "v1",
        "entity_id": "store-102",
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:00Z",
        "data_quality_score": None,
        "confidence": "",
    }
    record_none = model_ready_record_from_mapping(row_none)
    assert record_none.data_quality_score is None
    assert record_none.confidence is None

    # 3. Explicit numeric values (including 0.0) are parsed faithfully
    row_values: dict[str, object] = {
        "view_name": "forecast_training_view",
        "view_version": "v1",
        "entity_id": "store-103",
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:00Z",
        "data_quality_score": "0.0",
        "confidence": 0.88,
    }
    record_values = model_ready_record_from_mapping(row_values)
    assert record_values.data_quality_score == 0.0
    assert record_values.confidence == 0.88


def test_validate_quality_admission_identifies_missing_fields() -> None:
    base_row = {
        "view_name": "forecast_training_view",
        "view_version": "v1",
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:00Z",
        "source_snapshot_ids": ["txn-20260626"],
    }
    rows = [
        {**base_row, "entity_id": "store-ok", "data_quality_score": 0.9, "confidence": 0.9},
        {**base_row, "entity_id": "store-no-quality", "confidence": 0.9},
        {**base_row, "entity_id": "store-no-confidence", "data_quality_score": 0.9},
        {**base_row, "entity_id": "store-no-both"},
    ]
    issues = validate_quality_admission(rows)
    assert len(issues) == 3

    assert issues[0].entity_id == "store-no-quality"
    assert issues[0].missing_fields == ("data_quality_score",)
    assert "missing quality fields: data_quality_score" in issues[0].message

    assert issues[1].entity_id == "store-no-confidence"
    assert issues[1].missing_fields == ("confidence",)
    assert "missing quality fields: confidence" in issues[1].message

    assert issues[2].entity_id == "store-no-both"
    assert issues[2].missing_fields == ("data_quality_score", "confidence")
    assert "missing quality fields: data_quality_score, confidence" in issues[2].message


def test_build_dataset_snapshot_rejects_missing_data_quality_score() -> None:
    assert issubclass(DatasetQualityAdmissionError, DatasetSnapshotError)
    row = {
        "view_name": "forecast_training_view",
        "view_version": "v1",
        "entity_id": "store-1",
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:00Z",
        "source_snapshot_ids": ["txn-20260626"],
        "confidence": 0.95,
    }
    with pytest.raises(DatasetQualityAdmissionError) as exc_info:
        build_dataset_snapshot([row])

    err_msg = str(exc_info.value)
    assert "store-1" in err_msg
    assert "data_quality_score" in err_msg


def test_build_dataset_snapshot_rejects_missing_confidence() -> None:
    row = {
        "view_name": "forecast_training_view",
        "view_version": "v1",
        "entity_id": "store-2",
        "feature_snapshot_time": "2026-06-27T00:00:00Z",
        "prediction_origin_time": "2026-06-27T00:00:00Z",
        "source_snapshot_ids": ["txn-20260626"],
        "data_quality_score": 0.95,
    }
    with pytest.raises(DatasetQualityAdmissionError) as exc_info:
        build_dataset_snapshot([row])

    err_msg = str(exc_info.value)
    assert "store-2" in err_msg
    assert "confidence" in err_msg


def test_build_dataset_snapshot_rejects_multiple_records_with_missing_fields() -> None:
    rows = [
        {
            "view_name": "forecast_training_view",
            "view_version": "v1",
            "entity_id": "store-a",
            "feature_snapshot_time": "2026-06-27T00:00:00Z",
            "prediction_origin_time": "2026-06-27T00:00:00Z",
            "source_snapshot_ids": ["txn-20260626"],
            "confidence": 0.9,
        },
        {
            "view_name": "forecast_training_view",
            "view_version": "v1",
            "entity_id": "store-b",
            "feature_snapshot_time": "2026-06-27T00:00:00Z",
            "prediction_origin_time": "2026-06-27T00:00:00Z",
            "source_snapshot_ids": ["txn-20260626"],
            "data_quality_score": 0.9,
        },
    ]
    with pytest.raises(DatasetQualityAdmissionError) as exc_info:
        build_dataset_snapshot(rows)

    err_msg = str(exc_info.value)
    assert "store-a" in err_msg
    assert "data_quality_score" in err_msg
    assert "store-b" in err_msg
    assert "confidence" in err_msg


def test_dataset_snapshot_admission_complete_data_produces_immutable_receipt() -> None:
    rows = [
        {
            "view_name": "forecast_training_view",
            "view_version": "v1",
            "entity_id": "store-1",
            "feature_snapshot_time": "2026-06-27T00:00:00Z",
            "prediction_origin_time": "2026-06-27T00:00:00Z",
            "source_snapshot_ids": ["txn-20260626"],
            "data_quality_score": 0.92,
            "confidence": 0.88,
        },
        {
            "view_name": "forecast_training_view",
            "view_version": "v1",
            "entity_id": "store-2",
            "feature_snapshot_time": "2026-06-27T00:00:00Z",
            "prediction_origin_time": "2026-06-27T00:00:00Z",
            "source_snapshot_ids": ["txn-20260626"],
            "data_quality_score": 1.0,
            "confidence": 0.95,
        },
    ]
    snapshot = build_dataset_snapshot(rows)

    assert snapshot.dataset_snapshot_id.startswith("ds_")
    assert snapshot.entity_count == 2
    assert snapshot.training_record_count == 2
    assert snapshot.scoring_record_count == 2
    assert len(snapshot.records) == 2
    assert snapshot.records[0].data_quality_score == 0.92
    assert snapshot.records[0].confidence == 0.88
    assert snapshot.records[1].data_quality_score == 1.0
    assert snapshot.records[1].confidence == 0.95
