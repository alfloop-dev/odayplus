"""Learning Hub domain primitives."""

from modules.learninghub.domain.backtest import (
    BacktestReceipt,
    evaluate_backtest_run,
)
from modules.learninghub.domain.dataset_snapshot import (
    DatasetQualityAdmissionError,
    DatasetSnapshot,
    DatasetSnapshotError,
    DqTriageRecord,
    ModelReadyRecord,
    PointInTimeIssue,
    PointInTimeViolation,
    QualityAdmissionIssue,
    build_dataset_snapshot,
    model_ready_record_from_mapping,
    validate_point_in_time,
    validate_quality_admission,
)
from modules.learninghub.domain.feature_registry import (
    FeatureLineageEvent,
    FeatureRegistry,
    FeatureRegistryError,
    FeatureStatus,
    FeatureViewBinding,
    active_features_for_model,
    create_feature_registry,
    feature_usages_in_snapshot,
    has_blocked_features,
)
from modules.learninghub.domain.inference import (
    InferenceComparison,
    InferenceComparisonMode,
    InferenceDelta,
    InferencePrediction,
)
from modules.learninghub.domain.monitoring import (
    MonitoringBreach,
    MonitoringEvaluation,
    MonitoringSignalType,
    RetrainingRequest,
)

__all__ = [
    # backtest
    "BacktestReceipt",
    "evaluate_backtest_run",
    # dataset_snapshot
    "DatasetQualityAdmissionError",
    "DatasetSnapshot",
    "DatasetSnapshotError",
    "DqTriageRecord",
    "InferenceComparison",
    "InferenceComparisonMode",
    "InferenceDelta",
    "InferencePrediction",
    "ModelReadyRecord",
    "MonitoringBreach",
    "MonitoringEvaluation",
    "MonitoringSignalType",
    "PointInTimeIssue",
    "PointInTimeViolation",
    "QualityAdmissionIssue",
    "RetrainingRequest",
    "build_dataset_snapshot",
    "model_ready_record_from_mapping",
    "validate_point_in_time",
    "validate_quality_admission",
    # feature_registry
    "FeatureLineageEvent",
    "FeatureRegistry",
    "FeatureRegistryError",
    "FeatureStatus",
    "FeatureViewBinding",
    "active_features_for_model",
    "create_feature_registry",
    "feature_usages_in_snapshot",
    "has_blocked_features",
]
