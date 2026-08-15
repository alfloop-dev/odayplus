"""Integration tests for the Feature Registry.

Covers:
  - Registration and duplicate-version guard
  - Full status lifecycle: DRAFT -> ACTIVE -> DEPRECATED / BLOCKED
  - Lineage event log: per-version and cross-version full lineage
  - Annotation (non-status lineage events)
  - FeatureSet registration and model-name filtering
  - Model-ready view binding via bind_to_snapshot
  - Blocked-feature detection via has_blocked_features domain helper
  - active_features_for_model domain helper
  - feature_usages_in_snapshot domain helper
  - export_snapshot serialisation round-trip (structure check)
  - Pairing with LabelDefinition (symmetric dual-registry)

Task: ODP-FIN-ML-001
Owner: Antigravity3
Reviewer: Antigravity4
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from models.shared_ml import (
    FeatureDefinition,
    FeatureRegistryError,
    FeatureSet,
    LabelDefinition,
    create_feature_registry,
)
from modules.learninghub.domain.feature_registry import (
    active_features_for_model,
    feature_usages_in_snapshot,
    has_blocked_features,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SNAP_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)


def _feature(
    name: str = "geo.population_resident_500m",
    version: str = "1.0.0",
    status: str = "DRAFT",
    domain: str = "GEO",
    owner: str = "data-team",
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=f"feat-{name.replace('.', '-')}-{version}",
        feature_name=name,
        version=version,
        status=status,
        owner=owner,
        domain=domain,
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


def _feature_set(
    feature_set_id: str = "fs_model_v1",
    model_name: str = "sitescore",
    features: tuple[str, ...] = ("geo.population_resident_500m@1.0.0",),
) -> FeatureSet:
    return FeatureSet(
        feature_set_id=feature_set_id,
        model_name=model_name,
        version="1.0.0",
        features=features,
        point_in_time_policy_id="pit_v1",
    )


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_register_and_get() -> None:
    reg = create_feature_registry()
    feat = _feature()
    result = reg.register(feat, registered_by="ops")
    assert result.feature_name == "geo.population_resident_500m"
    assert result.status == "DRAFT"

    retrieved = reg.get("geo.population_resident_500m", "1.0.0")
    assert retrieved is not None
    assert retrieved.feature_id == feat.feature_id


def test_register_duplicate_version_raises() -> None:
    reg = create_feature_registry()
    feat = _feature()
    reg.register(feat)
    with pytest.raises(FeatureRegistryError, match="already registered"):
        reg.register(feat)


def test_register_multiple_versions() -> None:
    reg = create_feature_registry()
    reg.register(_feature(version="1.0.0"))
    reg.register(_feature(version="2.0.0"))

    versions = reg.list_versions("geo.population_resident_500m")
    assert len(versions) == 2
    assert [v.version for v in versions] == ["1.0.0", "2.0.0"]


def test_get_latest_version() -> None:
    """get() without version should return the lexicographically latest version."""
    reg = create_feature_registry()
    reg.register(_feature(version="1.0.0"))
    reg.register(_feature(version="2.0.0"))

    latest = reg.get("geo.population_resident_500m")
    assert latest is not None
    assert latest.version == "2.0.0"


def test_get_nonexistent_returns_none() -> None:
    reg = create_feature_registry()
    assert reg.get("nonexistent.feature") is None


# ---------------------------------------------------------------------------
# Status lifecycle tests
# ---------------------------------------------------------------------------


def test_approve_draft_feature() -> None:
    reg = create_feature_registry()
    reg.register(_feature())
    approved = reg.approve("geo.population_resident_500m", approved_by="steward")
    assert approved.status == "ACTIVE"
    assert approved.approved_by == "steward"


def test_approve_is_idempotent() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    result = reg.approve("geo.population_resident_500m", approved_by="steward")
    assert result.status == "ACTIVE"


def test_approve_blocked_feature_raises() -> None:
    reg = create_feature_registry()
    reg.register(_feature())
    reg.block("geo.population_resident_500m", blocked_by="security", reason="PII issue")
    with pytest.raises(FeatureRegistryError, match="BLOCKED"):
        reg.approve("geo.population_resident_500m", approved_by="steward")


def test_deprecate_active_feature() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    deprecated = reg.deprecate(
        "geo.population_resident_500m", deprecated_by="data-team", note="replaced by v2"
    )
    assert deprecated.status == "DEPRECATED"


def test_block_feature() -> None:
    reg = create_feature_registry()
    reg.register(_feature())
    blocked = reg.block(
        "geo.population_resident_500m",
        blocked_by="security",
        reason="PII detected",
    )
    assert blocked.status == "BLOCKED"


def test_deprecate_blocked_feature_raises() -> None:
    reg = create_feature_registry()
    reg.register(_feature())
    reg.block("geo.population_resident_500m", blocked_by="security", reason="hold")
    with pytest.raises(FeatureRegistryError, match="BLOCKED"):
        reg.deprecate("geo.population_resident_500m", deprecated_by="owner")


# ---------------------------------------------------------------------------
# Lineage tests
# ---------------------------------------------------------------------------


def test_lineage_captures_registration_event() -> None:
    reg = create_feature_registry()
    reg.register(_feature(), registered_by="pipeline", note="initial import")
    events = reg.lineage("geo.population_resident_500m")
    assert len(events) == 1
    assert events[0].event_type == "registered"
    assert events[0].changed_by == "pipeline"
    assert events[0].note == "initial import"
    assert events[0].previous_status is None
    assert events[0].new_status == "DRAFT"


def test_lineage_accumulates_transitions() -> None:
    reg = create_feature_registry()
    reg.register(_feature())
    reg.approve("geo.population_resident_500m", approved_by="steward")
    reg.deprecate("geo.population_resident_500m", deprecated_by="owner", note="v2 is out")

    events = reg.lineage("geo.population_resident_500m")
    assert len(events) == 3
    event_types = [e.event_type for e in events]
    assert event_types == ["registered", "approved", "deprecated"]


def test_lineage_annotation_does_not_change_status() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    reg.annotate(
        "geo.population_resident_500m",
        annotated_by="ml-team",
        note="used in sitescore v3 training",
    )
    events = reg.lineage("geo.population_resident_500m")
    assert events[-1].event_type == "annotated"
    assert events[-1].previous_status == "ACTIVE"
    assert events[-1].new_status == "ACTIVE"

    # Status must be unchanged
    feature = reg.get("geo.population_resident_500m")
    assert feature is not None
    assert feature.status == "ACTIVE"


def test_full_lineage_across_versions() -> None:
    reg = create_feature_registry()
    reg.register(_feature(version="1.0.0"))
    reg.approve("geo.population_resident_500m", version="1.0.0", approved_by="steward")
    reg.register(_feature(version="2.0.0"))
    reg.approve("geo.population_resident_500m", version="2.0.0", approved_by="steward")

    all_events = reg.full_lineage("geo.population_resident_500m")
    assert len(all_events) == 4
    versions_touched = {e.version for e in all_events}
    assert "1.0.0" in versions_touched
    assert "2.0.0" in versions_touched


def test_lineage_for_unknown_feature_returns_empty() -> None:
    reg = create_feature_registry()
    assert reg.lineage("nonexistent") == []


def test_lineage_event_to_dict_structure() -> None:
    reg = create_feature_registry()
    reg.register(_feature(), registered_by="ops")
    event = reg.lineage("geo.population_resident_500m")[0]
    d = event.to_dict()
    assert d["event_type"] == "registered"
    assert d["changed_by"] == "ops"
    assert "occurred_at" in d


# ---------------------------------------------------------------------------
# FeatureSet tests
# ---------------------------------------------------------------------------


def test_register_and_get_feature_set() -> None:
    reg = create_feature_registry()
    fs = _feature_set()
    reg.register_feature_set(fs)
    result = reg.get_feature_set("fs_model_v1")
    assert result is not None
    assert result.model_name == "sitescore"


def test_list_feature_sets_by_model() -> None:
    reg = create_feature_registry()
    reg.register_feature_set(_feature_set("fs_sitescore_v1", "sitescore"))
    reg.register_feature_set(_feature_set("fs_forecast_v1", "forecast"))
    reg.register_feature_set(_feature_set("fs_sitescore_v2", "sitescore"))

    sitescore_sets = reg.list_feature_sets(model_name="sitescore")
    assert len(sitescore_sets) == 2
    assert all(fs.model_name == "sitescore" for fs in sitescore_sets)


def test_list_all_feature_sets() -> None:
    reg = create_feature_registry()
    reg.register_feature_set(_feature_set("fs_a", "model_a"))
    reg.register_feature_set(_feature_set("fs_b", "model_b"))
    assert len(reg.list_feature_sets()) == 2


# ---------------------------------------------------------------------------
# View binding tests
# ---------------------------------------------------------------------------


def test_bind_to_snapshot_creates_bindings() -> None:
    reg = create_feature_registry()
    reg.register(_feature(version="1.0.0", status="ACTIVE"))
    fs = _feature_set()
    reg.register_feature_set(fs)

    bindings = reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-001",
        view_name="store_machine_timeseries_view",
        view_version="v1",
    )
    assert len(bindings) == 1
    assert bindings[0].feature_name == "geo.population_resident_500m"
    assert bindings[0].feature_version == "1.0.0"
    assert bindings[0].view_name == "store_machine_timeseries_view"
    assert bindings[0].dataset_snapshot_id == "ds-001"


def test_bind_to_snapshot_unknown_feature_set_raises() -> None:
    reg = create_feature_registry()
    with pytest.raises(FeatureRegistryError, match="not found"):
        reg.bind_to_snapshot(
            feature_set_id="nonexistent",
            dataset_snapshot_id="ds-001",
            view_name="view",
            view_version="v1",
        )


def test_bindings_for_feature() -> None:
    reg = create_feature_registry()
    reg.register(_feature(version="1.0.0", status="ACTIVE"))
    reg.register_feature_set(_feature_set())
    reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-001",
        view_name="view_a",
        view_version="v1",
    )
    reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-002",
        view_name="view_b",
        view_version="v1",
    )

    bindings = reg.bindings_for_feature("geo.population_resident_500m")
    assert len(bindings) == 2


def test_bindings_for_view() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    reg.register_feature_set(_feature_set())
    reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-001",
        view_name="store_machine_timeseries_view",
        view_version="v1",
    )

    bindings = reg.bindings_for_view("store_machine_timeseries_view")
    assert len(bindings) == 1
    bindings_versioned = reg.bindings_for_view("store_machine_timeseries_view", "v1")
    assert len(bindings_versioned) == 1
    bindings_wrong_version = reg.bindings_for_view("store_machine_timeseries_view", "v99")
    assert len(bindings_wrong_version) == 0


def test_bindings_for_snapshot() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    reg.register_feature_set(_feature_set())
    reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-42",
        view_name="v",
        view_version="1",
    )

    bindings = reg.bindings_for_snapshot("ds-42")
    assert len(bindings) == 1
    assert reg.bindings_for_snapshot("ds-unknown") == []


def test_view_binding_to_dict() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    reg.register_feature_set(_feature_set())
    bindings = reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-001",
        view_name="view_a",
        view_version="v1",
    )
    d = bindings[0].to_dict()
    assert d["feature_name"] == "geo.population_resident_500m"
    assert d["dataset_snapshot_id"] == "ds-001"
    assert "bound_at" in d


# ---------------------------------------------------------------------------
# has_blocked_features domain helper
# ---------------------------------------------------------------------------


def test_has_blocked_features_when_blocked() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="BLOCKED"))
    reg.register_feature_set(_feature_set())

    blocked, names = has_blocked_features(reg, "fs_model_v1")
    assert blocked is True
    assert "geo.population_resident_500m" in names


def test_has_blocked_features_when_active() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    reg.register_feature_set(_feature_set())

    blocked, names = has_blocked_features(reg, "fs_model_v1")
    assert blocked is False
    assert names == []


def test_has_blocked_features_unknown_set_raises() -> None:
    reg = create_feature_registry()
    with pytest.raises(FeatureRegistryError, match="not found"):
        has_blocked_features(reg, "nonexistent_fs")


# ---------------------------------------------------------------------------
# active_features_for_model domain helper
# ---------------------------------------------------------------------------


def test_active_features_for_model() -> None:
    reg = create_feature_registry()
    reg.register(_feature(name="feat_a", status="ACTIVE"))
    reg.register(_feature(name="feat_b", status="DRAFT"))
    reg.register_feature_set(
        FeatureSet(
            feature_set_id="fs_m1",
            model_name="mymodel",
            version="1.0.0",
            features=("feat_a@1.0.0", "feat_b@1.0.0"),
            point_in_time_policy_id="pit_v1",
        )
    )

    active = active_features_for_model(reg, "mymodel")
    assert len(active) == 1
    assert active[0].feature_name == "feat_a"


def test_active_features_for_model_no_sets() -> None:
    reg = create_feature_registry()
    result = active_features_for_model(reg, "unknown_model")
    assert result == []


# ---------------------------------------------------------------------------
# feature_usages_in_snapshot domain helper
# ---------------------------------------------------------------------------


def test_feature_usages_in_snapshot_returns_summary() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"))
    reg.register_feature_set(_feature_set())
    reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-99",
        view_name="store_view",
        view_version="v2",
    )

    usages = feature_usages_in_snapshot(reg, "ds-99")
    assert len(usages) == 1
    assert usages[0]["feature_name"] == "geo.population_resident_500m"
    assert usages[0]["status"] == "ACTIVE"
    assert usages[0]["view_name"] == "store_view"
    assert usages[0]["dataset_snapshot_id"] == "ds-99"


def test_feature_usages_in_snapshot_empty() -> None:
    reg = create_feature_registry()
    result = feature_usages_in_snapshot(reg, "ds-nonexistent")
    assert result == []


# ---------------------------------------------------------------------------
# list_all filtering
# ---------------------------------------------------------------------------


def test_list_all_filter_by_status() -> None:
    reg = create_feature_registry()
    reg.register(_feature(name="feat_a", status="ACTIVE"))
    reg.register(_feature(name="feat_b", status="DRAFT"))

    active = reg.list_all(status="ACTIVE")
    assert len(active) == 1
    assert active[0].feature_name == "feat_a"


def test_list_all_filter_by_domain() -> None:
    reg = create_feature_registry()
    reg.register(_feature(name="geo_feat", domain="GEO"))
    reg.register(_feature(name="ops_feat", domain="OPS"))

    geo = reg.list_all(domain="GEO")
    assert len(geo) == 1
    assert geo[0].domain == "GEO"


def test_list_all_filter_by_owner() -> None:
    reg = create_feature_registry()
    reg.register(_feature(name="f1", owner="team-a"))
    reg.register(_feature(name="f2", owner="team-b"))

    team_a = reg.list_all(owner="team-a")
    assert len(team_a) == 1
    assert team_a[0].feature_name == "f1"


# ---------------------------------------------------------------------------
# export_snapshot serialisation
# ---------------------------------------------------------------------------


def test_export_snapshot_structure() -> None:
    reg = create_feature_registry()
    reg.register(_feature(status="ACTIVE"), registered_by="ci")
    reg.register_feature_set(_feature_set())
    reg.bind_to_snapshot(
        feature_set_id="fs_model_v1",
        dataset_snapshot_id="ds-snap",
        view_name="view_x",
        view_version="v1",
    )

    snapshot = reg.export_snapshot()
    assert "features" in snapshot
    assert "lineage" in snapshot
    assert "feature_sets" in snapshot
    assert "bindings" in snapshot

    assert len(snapshot["features"]) == 1
    assert len(snapshot["lineage"]) == 1
    assert len(snapshot["feature_sets"]) == 1
    assert len(snapshot["bindings"]) == 1


# ---------------------------------------------------------------------------
# Symmetric dual-registry: Feature + Label pairing
# ---------------------------------------------------------------------------


def test_feature_and_label_registry_are_symmetric() -> None:
    """Both Feature and Label registries should be independently queryable and
    support the same DRAFT -> ACTIVE lifecycle."""
    from modules.learninghub import LearningHubService

    service = LearningHubService()

    # Feature side
    feature = _feature(status="DRAFT")
    service.create_feature(feature)
    service.approve_feature("geo.population_resident_500m", approved_by="steward")
    active_feat = service.get_feature("geo.population_resident_500m")
    assert active_feat is not None
    assert active_feat.status == "ACTIVE"

    # Label side — mirror the same lifecycle
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
    service.create_label(label)
    service.approve_label("w4_revenue", approved_by="model-board")
    active_label = service.get_label("w4_revenue")
    assert active_label is not None
    assert active_label.status == "ACTIVE"

    # Both registries populated and independently iterable
    features = service.list_features()
    labels = service.list_labels()
    assert len(features) == 1
    assert len(labels) == 1
    assert features[0].feature_name == "geo.population_resident_500m"
    assert labels[0].label_name == "w4_revenue"
