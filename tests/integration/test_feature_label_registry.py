from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime

import pytest

from models.shared_ml import (
    FeatureDefinition,
    FeatureSet,
    LabelDefinition,
    LabelSet,
    LocalModelArtifactStore,
    MetricThreshold,
    ModelCard,
    ModelCardApproval,
    ModelRiskLevel,
    ModelVersion,
)
from modules.learninghub import (
    LearningHubError,
    LearningHubService,
)

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-001",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "labels": {"w4_revenue": 410_000},
            "label_maturity_time": SNAPSHOT_TIME.isoformat(),
            "features": {
                "event_time": SNAPSHOT_TIME.isoformat(),
                "geo.population_resident_500m": 1200,
            },
        }
    ]


def test_feature_registry_lifecycle() -> None:
    service = LearningHubService()
    feature = FeatureDefinition(
        feature_id="feat-pop-500m",
        feature_name="geo.population_resident_500m",
        version="1.0.0",
        status="DRAFT",
        owner="data-team",
        domain="GEO",
        entity_type="STORE",
        entity_key=("store_id",),
        grain="500m",
        value_type="INTEGER",
        unit="count",
        semantic_type="STATIC",
        source_table="geo_stats",
        source_view="geo_grid_view",
        source_system="postgres",
        calculation_sql_uri="s3://sql/pop.sql",
        feature_available_time_rule="immediate",
        refresh_frequency="MONTHLY",
    )

    # 1. 建立 Feature 草稿
    created = service.create_feature(feature)
    assert created.status == "DRAFT"

    # 2. 查詢
    retrieved = service.get_feature("geo.population_resident_500m", "1.0.0")
    assert retrieved is not None
    assert retrieved.feature_id == "feat-pop-500m"

    # 3. 查詢最新版
    latest = service.get_feature("geo.population_resident_500m")
    assert latest is not None
    assert latest.version == "1.0.0"

    # 4. 核准 feature definition
    approved = service.approve_feature("geo.population_resident_500m", approved_by="data-steward")
    assert approved.status == "ACTIVE"
    assert approved.approved_by == "data-steward"

    # 5. List
    feats = service.list_features()
    assert len(feats) == 1
    assert feats[0].status == "ACTIVE"


def test_label_registry_lifecycle() -> None:
    service = LearningHubService()
    label = LabelDefinition(
        label_id="lbl-rev-w4",
        label_name="w4_revenue",
        version="1.0.0",
        status="DRAFT",
        owner="model-team",
        entity_type="STORE",
        entity_key=("store_id",),
        outcome_definition="store gross revenue for next 28 days",
        outcome_unit="TWD",
        label_window_start_rule="prediction_origin_time",
        label_window_end_rule="prediction_origin_time + 28d",
        label_maturity_rule="prediction_origin_time + 28d + 7d",
        source_table="store_sales",
        calculation_sql_uri="s3://sql/rev.sql",
    )

    # 1. 建立 Label
    created = service.create_label(label)
    assert created.status == "DRAFT"

    # 2. 查詢
    retrieved = service.get_label("w4_revenue", "1.0.0")
    assert retrieved is not None
    assert retrieved.label_id == "lbl-rev-w4"

    # 3. 核准
    approved = service.approve_label("w4_revenue", approved_by="model-board")
    assert approved.status == "ACTIVE"

    # 4. List
    labels = service.list_labels()
    assert len(labels) == 1
    assert labels[0].status == "ACTIVE"


def test_dataset_snapshot_binding_validation() -> None:
    service = LearningHubService()

    # 註冊 Feature & Label Definitions
    service.create_feature(
        FeatureDefinition(
            feature_id="feat-pop-500m",
            feature_name="geo.population_resident_500m",
            version="1.0.0",
            status="ACTIVE",
            owner="data-team",
            domain="GEO",
            entity_type="STORE",
            entity_key=("store_id",),
            grain="500m",
            value_type="INTEGER",
            unit="count",
            semantic_type="STATIC",
            source_table="geo_stats",
            source_view="geo_grid_view",
            source_system="postgres",
            calculation_sql_uri="s3://sql/pop.sql",
            feature_available_time_rule="immediate",
            refresh_frequency="MONTHLY",
        )
    )
    service.create_label(
        LabelDefinition(
            label_id="lbl-rev-w4",
            label_name="w4_revenue",
            version="1.0.0",
            status="ACTIVE",
            owner="model-team",
            entity_type="STORE",
            entity_key=("store_id",),
            outcome_definition="store gross revenue for next 28 days",
            outcome_unit="TWD",
            label_window_start_rule="prediction_origin_time",
            label_window_end_rule="prediction_origin_time + 28d",
            label_maturity_rule="prediction_origin_time + 28d + 7d",
            source_table="store_sales",
            calculation_sql_uri="s3://sql/rev.sql",
        )
    )

    # 建立 Feature Set 和 Label Set
    f_set = service.create_feature_set(
        FeatureSet(
            feature_set_id="fs_sitescore_v1",
            model_name="sitescore",
            version="1.0.0",
            features=("geo.population_resident_500m@1.0.0",),
            point_in_time_policy_id="pit_v1",
        )
    )
    l_set = service.create_label_set(
        LabelSet(
            label_set_id="ls_sitescore_v1",
            labels=("w4_revenue@1.0.0",),
            maturity_policy="mature_v1",
        )
    )

    # 1. 註冊合格的 Dataset Snapshot (綁定成功)
    snapshot = service.register_dataset_snapshot(
        _rows(),
        dataset_snapshot_id="snapshot-001",
        feature_set_id=f_set.feature_set_id,
        label_set_id=l_set.label_set_id,
    )
    assert snapshot.feature_set_id == "fs_sitescore_v1"
    assert snapshot.label_set_id == "ls_sitescore_v1"

    # 2. 註冊含有未宣告 feature/label 的 dataset (預期拋出 LearningHubError)
    invalid_rows = [
        {
            "view_name": "store_machine_timeseries_view",
            "view_version": "v1",
            "entity_id": "store-001",
            "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "labels": {"w4_revenue": 410_000, "unregistered_label": 10},
            "features": {
                "geo.population_resident_500m": 1200,
            },
        }
    ]
    with pytest.raises(LearningHubError, match="not allowed"):
        service.register_dataset_snapshot(
            invalid_rows,
            dataset_snapshot_id="snapshot-invalid",
            feature_set_id=f_set.feature_set_id,
            label_set_id=l_set.label_set_id,
        )


def test_dataset_snapshot_blocked_status() -> None:
    service = LearningHubService()

    # 註冊一個 status 為 BLOCKED 的 Feature Definition
    service.create_feature(
        FeatureDefinition(
            feature_id="feat-pop-500m",
            feature_name="geo.population_resident_500m",
            version="1.0.0",
            status="BLOCKED",  # BLOCKED!
            owner="data-team",
            domain="GEO",
            entity_type="STORE",
            entity_key=("store_id",),
            grain="500m",
            value_type="INTEGER",
            unit="count",
            semantic_type="STATIC",
            source_table="geo_stats",
            source_view="geo_grid_view",
            source_system="postgres",
            calculation_sql_uri="s3://sql/pop.sql",
            feature_available_time_rule="immediate",
            refresh_frequency="MONTHLY",
        )
    )

    f_set = service.create_feature_set(
        FeatureSet(
            feature_set_id="fs_blocked",
            model_name="sitescore",
            version="1.0.0",
            features=("geo.population_resident_500m@1.0.0",),
            point_in_time_policy_id="pit_v1",
        )
    )

    # 註冊時應被品質阻擋，拋出 LearningHubError
    with pytest.raises(LearningHubError, match="BLOCKED and cannot be used"):
        service.register_dataset_snapshot(
            _rows(),
            dataset_snapshot_id="snapshot-blocked",
            feature_set_id=f_set.feature_set_id,
        )


def test_model_card_generator_and_artifact_store() -> None:
    service = LearningHubService()
    temp_dir = tempfile.mkdtemp()

    try:
        # Mock candidate registration
        snapshot = service.register_dataset_snapshot(
            _rows(),
            dataset_snapshot_id="ds-mc-test",
            require_training_eligible=False,
        )
        validation = service.validate_candidate(
            model_name="test_model",
            model_version="1.0.0",
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            metrics={"w4_smape": 0.11},
            baseline_metrics={"w4_smape": 0.15},
            thresholds=(MetricThreshold("w4_smape", max_value=0.12),),
        )

        model_version = ModelVersion(
            model_name="test_model",
            version="1.0.0",
            artifact_uri=f"file://{temp_dir}",
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            feature_schema_version="v1",
            label_version="v1",
            metrics={"w4_smape": 0.11},
        )

        model_card = ModelCard(
            model_name="test_model",
            model_version="1.0.0",
            owner="ml-team",
            risk_level=ModelRiskLevel.R3,
            intended_use="Use in test forecasting",
            not_intended_use="Do not use for production pricing",
            dataset_snapshot_id=snapshot.dataset_snapshot_id,
            validation_run_id=validation.validation_run_id,
            feature_set_id="fs_test_v1",
            label_set_id="ls_test_v1",
            training_period="2026-01-01/2026-05-31",
            validation_period="2026-06-01/2026-06-27",
            algorithm="gboost",
            baseline="naive",
            metrics_summary={"w4_smape": 0.11},
            rollback_conditions=("w4_smape > 0.15",),
            approvals=(ModelCardApproval(approver="reviewer", role="lead"),),
        )

        # 註冊模型，此時應自動呼叫 LocalModelArtifactStore
        service.register_model_version(
            model_version=model_version,
            model_card=model_card,
            validation_run=validation,
        )

        # 使用 LocalModelArtifactStore 驗證載入
        store = LocalModelArtifactStore()
        loaded_card = store.load_model_card(
            "test_model", "1.0.0", artifact_uri=model_version.artifact_uri
        )

        assert loaded_card is not None
        assert loaded_card.model_name == "test_model"
        assert loaded_card.owner == "ml-team"
        assert loaded_card.risk_level == ModelRiskLevel.R3
        assert loaded_card.feature_set_id == "fs_test_v1"
        assert loaded_card.label_set_id == "ls_test_v1"
        assert len(loaded_card.approvals) == 1
        assert loaded_card.approvals[0].approver == "reviewer"

    finally:
        shutil.rmtree(temp_dir)
