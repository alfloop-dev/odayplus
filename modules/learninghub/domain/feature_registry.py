"""Learning Hub domain — Feature Registry facade.

Re-exports the shared Feature Registry service types and provides
domain-level helpers that join Feature Registry state with
DatasetSnapshot and model-ready view metadata.

The separation mirrors how ``dataset_snapshot.py`` is a domain-layer
wrapper around the raw DatasetSnapshot primitives — keeping application
code thin and the domain model self-contained.

Source docs:
  docs_archive/06_ai_causal_optimization/ODP-ML-02_FEATURE_AND_LABEL_REGISTRY.md
"""

from __future__ import annotations

from typing import Any

from models.shared_ml.feature_registry import (
    FeatureLineageEvent,
    FeatureRegistry,
    FeatureRegistryError,
    FeatureStatus,
    FeatureViewBinding,
    create_feature_registry,
)
from models.shared_ml.registry import FeatureDefinition, FeatureSet

# ---------------------------------------------------------------------------
# Domain-level query helpers (stateless, pure functions)
# ---------------------------------------------------------------------------


def feature_usages_in_snapshot(
    registry: FeatureRegistry,
    dataset_snapshot_id: str,
) -> list[dict[str, Any]]:
    """Return a structured summary of features used in a dataset snapshot.

    For each binding tied to *dataset_snapshot_id*, resolves the current
    ``FeatureDefinition`` and annotates it with view metadata.

    Returns a list of dicts suitable for audit trails and model cards.
    """
    bindings = registry.bindings_for_snapshot(dataset_snapshot_id)
    result: list[dict[str, Any]] = []
    for binding in bindings:
        feature = registry.get(binding.feature_name, binding.feature_version)
        entry: dict[str, Any] = {
            "feature_name": binding.feature_name,
            "feature_version": binding.feature_version,
            "view_name": binding.view_name,
            "view_version": binding.view_version,
            "feature_set_id": binding.feature_set_id,
            "dataset_snapshot_id": dataset_snapshot_id,
            "bound_at": binding.bound_at.isoformat(),
            "status": feature.status if feature else "UNKNOWN",
            "domain": feature.domain if feature else None,
            "owner": feature.owner if feature else None,
            "pii_classification": feature.pii_classification if feature else None,
        }
        result.append(entry)
    return result


def active_features_for_model(
    registry: FeatureRegistry,
    model_name: str,
) -> list[FeatureDefinition]:
    """Return all ACTIVE features that appear in any FeatureSet for *model_name*.

    Used when building model cards or validating that a model's features are
    all still in ACTIVE status before a production release.
    """
    feature_sets = registry.list_feature_sets(model_name=model_name)
    feature_names: set[str] = set()
    for fs in feature_sets:
        for feat_ref in fs.features:
            feat_name = feat_ref.split("@")[0] if "@" in feat_ref else feat_ref
            feature_names.add(feat_name)

    active: list[FeatureDefinition] = []
    for name in sorted(feature_names):
        feature = registry.get(name)
        if feature and feature.status == FeatureStatus.ACTIVE:
            active.append(feature)
    return active


def has_blocked_features(
    registry: FeatureRegistry,
    feature_set_id: str,
) -> tuple[bool, list[str]]:
    """Check whether a FeatureSet contains any BLOCKED features.

    Returns ``(True, [list of blocked feature names])`` if any are blocked,
    otherwise ``(False, [])``.

    Called by the dataset-snapshot registration path to enforce the quality
    gate before creating a training or scoring snapshot.
    """
    feature_set = registry.get_feature_set(feature_set_id)
    if feature_set is None:
        raise FeatureRegistryError(
            f"feature set {feature_set_id!r} not found; register it first"
        )

    blocked: list[str] = []
    for feat_ref in feature_set.features:
        feat_name = feat_ref.split("@")[0] if "@" in feat_ref else feat_ref
        feat_version = feat_ref.split("@")[1] if "@" in feat_ref else None
        feature = registry.get(feat_name, feat_version)
        if feature and feature.status == FeatureStatus.BLOCKED:
            blocked.append(feat_name)

    return bool(blocked), blocked


# ---------------------------------------------------------------------------
# Re-export for callers that import from this domain module
# ---------------------------------------------------------------------------

__all__ = [
    # core types (re-exported from shared_ml for domain consumers)
    "FeatureDefinition",
    "FeatureLineageEvent",
    "FeatureRegistry",
    "FeatureRegistryError",
    "FeatureSet",
    "FeatureStatus",
    "FeatureViewBinding",
    "create_feature_registry",
    # domain-level helpers
    "active_features_for_model",
    "feature_usages_in_snapshot",
    "has_blocked_features",
]
