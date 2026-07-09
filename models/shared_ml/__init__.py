"""Shared ML lifecycle primitives."""

from models.shared_ml.artifact_store import (
    ArtifactKind,
    ArtifactRecord,
    ArtifactStore,
    InMemoryArtifactStore,
    ModelRegistryEvidence,
    artifact_uri,
    build_model_registry_evidence,
    compute_content_digest,
    make_artifact_id,
)
from models.shared_ml.model_card import ModelCard, ModelCardApproval, ModelRiskLevel
from models.shared_ml.registry import (
    ModelAlias,
    ModelRegistryError,
    ModelStage,
    ModelVersion,
    RegisteredModel,
)
from models.shared_ml.validation import (
    MetricThreshold,
    SegmentMetric,
    ValidationRuleFailure,
    ValidationRun,
    ValidationStatus,
    validate_model_candidate,
)

__all__ = [
    "ArtifactKind",
    "ArtifactRecord",
    "ArtifactStore",
    "InMemoryArtifactStore",
    "MetricThreshold",
    "ModelAlias",
    "ModelCard",
    "ModelCardApproval",
    "ModelRegistryError",
    "ModelRegistryEvidence",
    "ModelRiskLevel",
    "ModelStage",
    "ModelVersion",
    "RegisteredModel",
    "SegmentMetric",
    "ValidationRuleFailure",
    "ValidationRun",
    "ValidationStatus",
    "artifact_uri",
    "build_model_registry_evidence",
    "compute_content_digest",
    "make_artifact_id",
    "validate_model_candidate",
]
