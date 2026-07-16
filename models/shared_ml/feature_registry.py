"""Feature Registry — registration, versioning, lineage, and model-ready view binding.

Pairs with LabelDefinition / LabelSet in models.shared_ml.registry to give the
Learning Hub a symmetric Feature + Label dual-registry as required by ODP-ML-02.

Source docs:
  docs_archive/06_ai_causal_optimization/ODP-ML-02_FEATURE_AND_LABEL_REGISTRY.md
  docs_archive/03_data_integration/ODP-DATA-06_MODEL_READY_VIEWS_SPECIFICATION.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from models.shared_ml.registry import FeatureDefinition, FeatureSet


class FeatureRegistryError(ValueError):
    """Raised when a Feature Registry operation cannot be completed."""


# ---------------------------------------------------------------------------
# Status lifecycle enum
# ---------------------------------------------------------------------------


class FeatureStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# Lineage record — one entry per status-changing event on a feature version
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureLineageEvent:
    """Immutable record of a status transition or annotation on a feature version."""

    feature_name: str
    version: str
    event_type: str  # "registered" | "approved" | "deprecated" | "blocked" | "annotated"
    changed_by: str
    previous_status: str | None
    new_status: str | None
    note: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "version": self.version,
            "event_type": self.event_type,
            "changed_by": self.changed_by,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "note": self.note,
            "occurred_at": self.occurred_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Model-ready view binding — tracks which features appear in which views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureViewBinding:
    """Records that a feature version is used by a particular model-ready view.

    Binding is created when a dataset snapshot is registered with a feature set
    that references this feature.  The binding lets downstream code query all
    view versions that depend on a given feature, or all features used by a
    given view.
    """

    feature_name: str
    feature_version: str
    view_name: str
    view_version: str
    feature_set_id: str
    dataset_snapshot_id: str
    bound_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "feature_version": self.feature_version,
            "view_name": self.view_name,
            "view_version": self.view_version,
            "feature_set_id": self.feature_set_id,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "bound_at": self.bound_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Feature Registry service
# ---------------------------------------------------------------------------


class FeatureRegistry:
    """In-process Feature Registry — persists features, lineage, and view bindings.

    The registry is intentionally storage-neutral: the in-memory implementation
    is used by tests and by :class:`~modules.learninghub.application.LearningHubService`.
    A durable adapter (SQLite / PostgreSQL-backed) can satisfy the same interface.

    Symmetric counterpart to the label-registry surface exposed via
    ``LearningHubService.create_label`` / ``approve_label`` / ``list_labels``.
    """

    def __init__(self) -> None:
        # (feature_name, version) -> FeatureDefinition
        self._features: dict[tuple[str, str], FeatureDefinition] = {}
        # (feature_name, version) -> list[FeatureLineageEvent]
        self._lineage: dict[tuple[str, str], list[FeatureLineageEvent]] = {}
        # feature_set_id -> FeatureSet
        self._feature_sets: dict[str, FeatureSet] = {}
        # list of all view bindings
        self._bindings: list[FeatureViewBinding] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        feature: FeatureDefinition,
        *,
        registered_by: str = "system",
        note: str = "",
    ) -> FeatureDefinition:
        """Register a new feature version.

        Raises :class:`FeatureRegistryError` if the exact (name, version) pair
        already exists (use a new version string instead).
        """
        key = (feature.feature_name, feature.version)
        if key in self._features:
            raise FeatureRegistryError(
                f"feature {feature.feature_name!r} version {feature.version!r} "
                "already registered; bump the version to re-register"
            )
        self._features[key] = feature
        self._lineage.setdefault(key, []).append(
            FeatureLineageEvent(
                feature_name=feature.feature_name,
                version=feature.version,
                event_type="registered",
                changed_by=registered_by,
                previous_status=None,
                new_status=feature.status,
                note=note,
            )
        )
        return feature

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def approve(
        self,
        feature_name: str,
        *,
        version: str | None = None,
        approved_by: str,
        note: str = "",
    ) -> FeatureDefinition:
        """Transition a DRAFT feature to ACTIVE."""
        feature = self._resolve(feature_name, version)
        if feature.status == FeatureStatus.ACTIVE:
            return feature  # idempotent
        if feature.status == FeatureStatus.BLOCKED:
            raise FeatureRegistryError(
                f"feature {feature_name!r} is BLOCKED; unblock before approving"
            )
        return self._transition(
            feature,
            new_status=FeatureStatus.ACTIVE,
            event_type="approved",
            changed_by=approved_by,
            note=note,
            extra={"approved_by": approved_by},
        )

    def deprecate(
        self,
        feature_name: str,
        *,
        version: str | None = None,
        deprecated_by: str,
        note: str = "",
    ) -> FeatureDefinition:
        """Transition an ACTIVE (or DRAFT) feature to DEPRECATED."""
        feature = self._resolve(feature_name, version)
        if feature.status == FeatureStatus.BLOCKED:
            raise FeatureRegistryError(
                f"feature {feature_name!r} is BLOCKED; cannot deprecate directly"
            )
        return self._transition(
            feature,
            new_status=FeatureStatus.DEPRECATED,
            event_type="deprecated",
            changed_by=deprecated_by,
            note=note,
        )

    def block(
        self,
        feature_name: str,
        *,
        version: str | None = None,
        blocked_by: str,
        reason: str,
    ) -> FeatureDefinition:
        """Transition any feature to BLOCKED (quality gate / PII / security hold)."""
        feature = self._resolve(feature_name, version)
        return self._transition(
            feature,
            new_status=FeatureStatus.BLOCKED,
            event_type="blocked",
            changed_by=blocked_by,
            note=reason,
        )

    def annotate(
        self,
        feature_name: str,
        *,
        version: str | None = None,
        annotated_by: str,
        note: str,
    ) -> FeatureDefinition:
        """Add a lineage annotation without changing feature status."""
        feature = self._resolve(feature_name, version)
        key = (feature.feature_name, feature.version)
        self._lineage.setdefault(key, []).append(
            FeatureLineageEvent(
                feature_name=feature.feature_name,
                version=feature.version,
                event_type="annotated",
                changed_by=annotated_by,
                previous_status=feature.status,
                new_status=feature.status,
                note=note,
            )
        )
        return feature

    # ------------------------------------------------------------------
    # FeatureSet management
    # ------------------------------------------------------------------

    def register_feature_set(self, feature_set: FeatureSet) -> FeatureSet:
        """Register a versioned FeatureSet grouping features for a model."""
        self._feature_sets[feature_set.feature_set_id] = feature_set
        return feature_set

    def get_feature_set(self, feature_set_id: str) -> FeatureSet | None:
        return self._feature_sets.get(feature_set_id)

    def list_feature_sets(self, *, model_name: str | None = None) -> list[FeatureSet]:
        sets = list(self._feature_sets.values())
        if model_name is not None:
            sets = [s for s in sets if s.model_name == model_name]
        return sets

    # ------------------------------------------------------------------
    # Model-ready view binding
    # ------------------------------------------------------------------

    def bind_to_snapshot(
        self,
        *,
        feature_set_id: str,
        dataset_snapshot_id: str,
        view_name: str,
        view_version: str,
    ) -> list[FeatureViewBinding]:
        """Create view-binding records for every feature in the feature set.

        Called by the Learning Hub service when a dataset snapshot is registered
        with a feature_set_id so that lineage can be traced from feature -> view
        -> snapshot -> model version.

        Returns the list of newly created bindings (may be empty if feature set
        contains no registered features).
        """
        feature_set = self._feature_sets.get(feature_set_id)
        if feature_set is None:
            raise FeatureRegistryError(
                f"feature set {feature_set_id!r} not found; register it first"
            )

        new_bindings: list[FeatureViewBinding] = []
        for feat_ref in feature_set.features:
            feat_name = feat_ref.split("@")[0] if "@" in feat_ref else feat_ref
            feat_version = feat_ref.split("@")[1] if "@" in feat_ref else None

            # Resolve the feature; silently skip if unregistered (soft binding)
            try:
                feature = self._resolve(feat_name, feat_version)
            except FeatureRegistryError:
                continue

            binding = FeatureViewBinding(
                feature_name=feature.feature_name,
                feature_version=feature.version,
                view_name=view_name,
                view_version=view_version,
                feature_set_id=feature_set_id,
                dataset_snapshot_id=dataset_snapshot_id,
            )
            self._bindings.append(binding)
            new_bindings.append(binding)

        return new_bindings

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(
        self, feature_name: str, version: str | None = None
    ) -> FeatureDefinition | None:
        """Return the requested feature version, or the latest if version is None."""
        if version:
            return self._features.get((feature_name, version))
        matches = [f for (name, _), f in self._features.items() if name == feature_name]
        if not matches:
            return None
        return max(matches, key=lambda f: f.version)

    def list_versions(self, feature_name: str) -> list[FeatureDefinition]:
        """Return all registered versions of *feature_name* sorted by version."""
        versions = [f for (name, _), f in self._features.items() if name == feature_name]
        return sorted(versions, key=lambda f: f.version)

    def list_all(
        self,
        *,
        status: str | None = None,
        domain: str | None = None,
        owner: str | None = None,
    ) -> list[FeatureDefinition]:
        """Return all registered features, optionally filtered."""
        result = list(self._features.values())
        if status is not None:
            result = [f for f in result if f.status == status]
        if domain is not None:
            result = [f for f in result if f.domain == domain]
        if owner is not None:
            result = [f for f in result if f.owner == owner]
        return result

    def lineage(
        self, feature_name: str, version: str | None = None
    ) -> list[FeatureLineageEvent]:
        """Return the ordered lineage event log for the specified feature version.

        If *version* is None, returns events for the latest version.
        """
        feature = self.get(feature_name, version)
        if feature is None:
            return []
        key = (feature_name, feature.version)
        return list(self._lineage.get(key, []))

    def full_lineage(self, feature_name: str) -> list[FeatureLineageEvent]:
        """Return all lineage events across all versions of *feature_name*, sorted
        by occurrence time."""
        all_events: list[FeatureLineageEvent] = []
        for (name, _), events in self._lineage.items():
            if name == feature_name:
                all_events.extend(events)
        return sorted(all_events, key=lambda e: e.occurred_at)

    def bindings_for_feature(
        self, feature_name: str, version: str | None = None
    ) -> list[FeatureViewBinding]:
        """Return all view bindings for a given feature (and optional version)."""
        matches = [b for b in self._bindings if b.feature_name == feature_name]
        if version is not None:
            matches = [b for b in matches if b.feature_version == version]
        return matches

    def bindings_for_view(
        self, view_name: str, view_version: str | None = None
    ) -> list[FeatureViewBinding]:
        """Return all feature bindings attached to a given model-ready view."""
        matches = [b for b in self._bindings if b.view_name == view_name]
        if view_version is not None:
            matches = [b for b in matches if b.view_version == view_version]
        return matches

    def bindings_for_snapshot(
        self, dataset_snapshot_id: str
    ) -> list[FeatureViewBinding]:
        """Return all feature bindings created for a given dataset snapshot."""
        return [b for b in self._bindings if b.dataset_snapshot_id == dataset_snapshot_id]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, feature_name: str, version: str | None) -> FeatureDefinition:
        feature = self.get(feature_name, version)
        if feature is None:
            suffix = f" version {version!r}" if version else ""
            raise FeatureRegistryError(f"unknown feature {feature_name!r}{suffix}")
        return feature

    def _transition(
        self,
        feature: FeatureDefinition,
        *,
        new_status: FeatureStatus,
        event_type: str,
        changed_by: str,
        note: str,
        extra: dict[str, Any] | None = None,
    ) -> FeatureDefinition:
        key = (feature.feature_name, feature.version)
        previous_status = feature.status

        # Rebuild FeatureDefinition with new status (frozen dataclass)
        kwargs: dict[str, Any] = {
            "feature_id": feature.feature_id,
            "feature_name": feature.feature_name,
            "version": feature.version,
            "status": new_status.value,
            "owner": feature.owner,
            "domain": feature.domain,
            "entity_type": feature.entity_type,
            "entity_key": feature.entity_key,
            "grain": feature.grain,
            "value_type": feature.value_type,
            "unit": feature.unit,
            "semantic_type": feature.semantic_type,
            "source_table": feature.source_table,
            "source_view": feature.source_view,
            "source_system": feature.source_system,
            "calculation_sql_uri": feature.calculation_sql_uri,
            "feature_available_time_rule": feature.feature_available_time_rule,
            "refresh_frequency": feature.refresh_frequency,
            "allowed_model_names": feature.allowed_model_names,
            "forbidden_model_names": feature.forbidden_model_names,
            "quality_rules": feature.quality_rules,
            "null_policy": feature.null_policy,
            "pii_classification": feature.pii_classification,
            "lineage": feature.lineage,
            "created_at": feature.created_at,
            "updated_at": datetime.now(UTC),
            "approved_by": feature.approved_by,
        }
        if extra:
            kwargs.update(extra)

        updated = FeatureDefinition(**kwargs)
        self._features[key] = updated

        self._lineage.setdefault(key, []).append(
            FeatureLineageEvent(
                feature_name=feature.feature_name,
                version=feature.version,
                event_type=event_type,
                changed_by=changed_by,
                previous_status=previous_status,
                new_status=new_status.value,
                note=note,
            )
        )
        return updated

    # ------------------------------------------------------------------
    # Serialization helpers (for persistence adapters)
    # ------------------------------------------------------------------

    def export_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of registry state for persistence."""
        return {
            "features": [f.to_dict() for f in self._features.values()],
            "lineage": [
                evt.to_dict()
                for events in self._lineage.values()
                for evt in events
            ],
            "feature_sets": [fs.to_dict() for fs in self._feature_sets.values()],
            "bindings": [b.to_dict() for b in self._bindings],
        }


# ---------------------------------------------------------------------------
# Convenience factory: build a fresh FeatureRegistry
# ---------------------------------------------------------------------------


def create_feature_registry() -> FeatureRegistry:
    """Return an empty :class:`FeatureRegistry` instance."""
    return FeatureRegistry()


__all__ = [
    "FeatureLineageEvent",
    "FeatureRegistry",
    "FeatureRegistryError",
    "FeatureStatus",
    "FeatureViewBinding",
    "create_feature_registry",
]
