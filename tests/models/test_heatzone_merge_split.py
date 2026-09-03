"""Unit tests for HeatZone merge/split domain, readiness gate, and evaluation engine (ODP-FR-HZ-006)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.heatzone.application.merge_split_engine import (
    CandidateCellFeature,
    MergeSplitReadinessInput,
    check_readiness_gates,
    evaluate_merge_split,
)
from modules.heatzone.domain.composition import (
    CompositionKind,
    CompositionValidationError,
    HeatZoneCompositionRecord,
    generate_merged_zone_id,
)
from modules.heatzone.infrastructure.composition_repository import (
    InMemoryHeatZoneCompositionRepository,
)
from shared.governance import (
    default_heatzone_merge_policy,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import (
    DurableHeatZoneCompositionRepository,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def test_generate_merged_zone_id_format_and_determinism() -> None:
    cell_a = "cell-uuid-1"
    cell_b = "cell-uuid-2"

    zone_id_1 = generate_merged_zone_id([cell_a, cell_b])
    zone_id_2 = generate_merged_zone_id([cell_b, cell_a])

    assert zone_id_1 == zone_id_2
    assert zone_id_1.startswith("MZ-")
    assert len(zone_id_1) == 19  # MZ- (3) + 16 hex chars
    assert zone_id_1 != cell_a
    assert zone_id_1 != cell_b

    with pytest.raises(CompositionValidationError):
        generate_merged_zone_id([])


def test_composition_record_validation_rules() -> None:
    valid_zone_id = "MZ-1234567890abcdef"
    policy_id = f"heatzone-merge-v1:{TENANT_ID}"

    # 1. Valid system record
    rec_sys = HeatZoneCompositionRecord(
        zone_id=valid_zone_id,
        tenant_id=TENANT_ID,
        member_cell_id="cell-1",
        composition_kind=CompositionKind.MERGED,
        parent_zone_id=None,
        decided_by="system",
        decision_policy_version_id=policy_id,
        override_reason=None,
    )
    assert rec_sys.is_active is True

    # 2. Invalid zone_id format
    with pytest.raises(CompositionValidationError, match="does not match required format"):
        HeatZoneCompositionRecord(
            zone_id="invalid-zone-id",
            tenant_id=TENANT_ID,
            member_cell_id="cell-1",
            composition_kind=CompositionKind.MERGED,
            decision_policy_version_id=policy_id,
        )

    # 3. SPLIT_CHILD without parent_zone_id
    with pytest.raises(CompositionValidationError, match="SPLIT_CHILD composition must specify parent_zone_id"):
        HeatZoneCompositionRecord(
            zone_id=valid_zone_id,
            tenant_id=TENANT_ID,
            member_cell_id="cell-1",
            composition_kind=CompositionKind.SPLIT_CHILD,
            parent_zone_id=None,
            decision_policy_version_id=policy_id,
        )

    # 4. MERGED with parent_zone_id
    with pytest.raises(CompositionValidationError, match="must not have parent_zone_id"):
        HeatZoneCompositionRecord(
            zone_id=valid_zone_id,
            tenant_id=TENANT_ID,
            member_cell_id="cell-1",
            composition_kind=CompositionKind.MERGED,
            parent_zone_id="MZ-parent000000000",
            decision_policy_version_id=policy_id,
        )

    # 5. System decision with override_reason
    with pytest.raises(CompositionValidationError, match="System decision must not carry override_reason"):
        HeatZoneCompositionRecord(
            zone_id=valid_zone_id,
            tenant_id=TENANT_ID,
            member_cell_id="cell-1",
            composition_kind=CompositionKind.MERGED,
            decided_by="system",
            override_reason="Human reason",
            decision_policy_version_id=policy_id,
        )

    # 6. Human decision without override_reason
    with pytest.raises(CompositionValidationError, match="requires a non-empty override_reason"):
        HeatZoneCompositionRecord(
            zone_id=valid_zone_id,
            tenant_id=TENANT_ID,
            member_cell_id="cell-1",
            composition_kind=CompositionKind.MERGED,
            decided_by="operator@odayplus.com",
            override_reason=None,
            decision_policy_version_id=policy_id,
        )

    # 7. Revert before decided_at
    with pytest.raises(CompositionValidationError, match="cannot be earlier than decided_at"):
        HeatZoneCompositionRecord(
            zone_id=valid_zone_id,
            tenant_id=TENANT_ID,
            member_cell_id="cell-1",
            composition_kind=CompositionKind.MERGED,
            decided_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
            reverted_at=datetime(2026, 9, 3, 11, 0, tzinfo=UTC),
            decision_policy_version_id=policy_id,
        )

    # 8. Mismatched tenant in policy_version_id
    with pytest.raises(CompositionValidationError, match="does not belong to tenant"):
        HeatZoneCompositionRecord(
            zone_id=valid_zone_id,
            tenant_id=TENANT_ID,
            member_cell_id="cell-1",
            composition_kind=CompositionKind.MERGED,
            decision_policy_version_id="heatzone-merge-v1:other-tenant",
        )


def test_readiness_gate_fails_closed_when_evidence_immature() -> None:
    policy = default_heatzone_merge_policy(TENANT_ID)

    # Production current state: 0 mature labels, 0 days horizon
    empty_evidence = MergeSplitReadinessInput(
        observation_days=0,
        mature_labels_count=0,
        active_store_count=0,
        adjacent_pairs_count=0,
        metro_clusters_count=0,
        spatial_contiguity_ratio=0.0,
        absorption_ratio_cv=None,
        drift_psi=None,
        source_snapshot_id="",
    )

    result = check_readiness_gates(empty_evidence, policy)
    assert result.eligible is False
    assert any("observation_horizon_insufficient" in r for r in result.reasons)
    assert any("sample_size_insufficient" in r for r in result.reasons)
    assert any("missing_source_snapshot_id" in r for r in result.reasons)
    assert any("absorption_cv_unmeasured" in r for r in result.reasons)

    # Evaluation engine fails closed / abstains with reasons
    eval_res = evaluate_merge_split(
        [],
        readiness_input=empty_evidence,
        policy=policy,
    )
    assert eval_res.abstained is True
    assert len(eval_res.proposals) == 0
    assert eval_res.abstain_reasons == result.reasons


def test_readiness_gate_passes_when_all_thresholds_met() -> None:
    policy = default_heatzone_merge_policy(TENANT_ID)

    mature_evidence = MergeSplitReadinessInput(
        observation_days=185,
        mature_labels_count=240,
        active_store_count=65,
        adjacent_pairs_count=42,
        metro_clusters_count=3,
        spatial_contiguity_ratio=0.88,
        absorption_ratio_cv=0.11,
        drift_psi=0.04,
        wasserstein_distance=0.02,
        source_snapshot_id="snap-mature-20260903",
    )

    result = check_readiness_gates(mature_evidence, policy)
    assert result.eligible is True
    assert len(result.reasons) == 0


def test_evaluation_engine_generates_merge_and_split_proposals() -> None:
    policy = default_heatzone_merge_policy(TENANT_ID)
    mature_evidence = MergeSplitReadinessInput(
        observation_days=200,
        mature_labels_count=300,
        active_store_count=70,
        adjacent_pairs_count=50,
        metro_clusters_count=3,
        spatial_contiguity_ratio=0.90,
        absorption_ratio_cv=0.09,
        drift_psi=0.03,
        wasserstein_distance=0.02,
        source_snapshot_id="snap-mature-20260903",
    )

    # 2 adjacent cells with high demand correlation
    cell_a = CandidateCellFeature(
        cell_id="cell-uuid-1",
        h3_index="8928308280fffff",
        tenant_id=TENANT_ID,
        admin_city="Taipei",
        admin_district="Daan",
        population=12000.0,
        poi_count=45,
        own_store_count=1,
        competitor_count=2,
        unmet_demand=150.0,
        absorbed_demand=120.0,
        realized_revenue=850000.0,
        adjacent_cell_ids=("cell-uuid-2",),
    )
    cell_b = CandidateCellFeature(
        cell_id="cell-uuid-2",
        h3_index="8928308281fffff",
        tenant_id=TENANT_ID,
        admin_city="Taipei",
        admin_district="Daan",
        population=11500.0,
        poi_count=42,
        own_store_count=1,
        competitor_count=2,
        unmet_demand=145.0,
        absorbed_demand=115.0,
        realized_revenue=820000.0,
        adjacent_cell_ids=("cell-uuid-1",),
    )
    # 1 heterogeneous cell with natural barrier and empirical outcome disparity across sides
    cell_c = CandidateCellFeature(
        cell_id="cell-uuid-3",
        h3_index="8928308282fffff",
        tenant_id=TENANT_ID,
        admin_city="Taipei",
        admin_district="Neihu",
        population=9000.0,
        poi_count=20,
        absorbed_demand=80.0,
        realized_revenue=400000.0,
        has_natural_barrier=True,
        barrier_description="Keelung River & Elevated Expressway",
        barrier_side_a_revenue=320000.0,
        barrier_side_a_absorbed_demand=65.0,
        barrier_side_b_revenue=80000.0,
        barrier_side_b_absorbed_demand=15.0,
        child_partition_cell_ids=(("cell-uuid-3-north",), ("cell-uuid-3-south",)),
    )

    eval_result = evaluate_merge_split(
        [cell_a, cell_b, cell_c],
        readiness_input=mature_evidence,
        policy=policy,
    )

    assert eval_result.abstained is False
    assert len(eval_result.proposals) == 3  # 1 merge + 2 split child proposals

    # Check merge proposal
    merge_prop = next(p for p in eval_result.proposals if p.composition_kind == CompositionKind.MERGED)
    assert merge_prop.member_cell_ids == ("cell-uuid-1", "cell-uuid-2")
    assert merge_prop.ndcg_gain >= 0.05
    assert merge_prop.cannibalization_variance_reduction >= 0.20
    assert merge_prop.correlation_rho >= 0.75
    assert merge_prop.zone_id.startswith("MZ-")

    # Check split proposals (both children)
    split_props = [p for p in eval_result.proposals if p.composition_kind == CompositionKind.SPLIT_CHILD]
    assert len(split_props) == 2
    for split_prop in split_props:
        assert split_prop.parent_zone_id is not None
        assert split_prop.split_density_ratio >= 2.5
        assert "internal_natural_barrier_detected" in split_prop.reasons


def test_in_memory_composition_repository_lifecycle() -> None:
    repo = InMemoryHeatZoneCompositionRepository()
    zone_id = "MZ-aabbccddeeff0011"
    policy_id = f"heatzone-merge-v1:{TENANT_ID}"

    # 1. Save composition
    r1 = HeatZoneCompositionRecord(
        zone_id=zone_id,
        tenant_id=TENANT_ID,
        member_cell_id="cell-1",
        composition_kind=CompositionKind.MERGED,
        decision_policy_version_id=policy_id,
    )
    r2 = HeatZoneCompositionRecord(
        zone_id=zone_id,
        tenant_id=TENANT_ID,
        member_cell_id="cell-2",
        composition_kind=CompositionKind.MERGED,
        decision_policy_version_id=policy_id,
    )
    repo.save_composition_batch([r1, r2])

    active = repo.get_composition(zone_id, TENANT_ID)
    assert len(active) == 2
    assert repo.get_active_for_cell("cell-1", TENANT_ID) is not None

    # Duplicate active member insertion rejected
    r_dup = HeatZoneCompositionRecord(
        zone_id="MZ-9988776655443322",
        tenant_id=TENANT_ID,
        member_cell_id="cell-1",
        composition_kind=CompositionKind.MERGED,
        decision_policy_version_id=policy_id,
    )
    with pytest.raises(CompositionValidationError, match="already an active member"):
        repo.save_composition(r_dup)

    # 2. Human override
    overridden = repo.override_composition(
        zone_id=zone_id,
        tenant_id=TENANT_ID,
        decided_by="operator@odayplus.com",
        override_reason="Operator boundary adjustment based on local field survey",
        decision_policy_version_id=policy_id,
        new_cells=["cell-1", "cell-2", "cell-3"],
    )
    assert len(overridden) == 3
    assert all(r.decided_by == "operator@odayplus.com" for r in overridden)

    # Check lineage
    lineage = repo.get_lineage(zone_id, TENANT_ID)
    assert lineage is not None
    assert lineage.is_active is True
    assert lineage.decided_by == "operator@odayplus.com"
    assert len(lineage.records) == 5  # 2 original (now reverted) + 3 new active

    # 3. Rollback
    reverted = repo.revert_composition(zone_id, TENANT_ID)
    assert len(reverted) == 3
    assert all(not r.is_active for r in reverted)

    post_rollback_lineage = repo.get_lineage(zone_id, TENANT_ID)
    assert post_rollback_lineage is not None
    assert post_rollback_lineage.is_active is False


def test_durable_composition_repository_lifecycle(tmp_path) -> None:
    db_file = tmp_path / "durable_comp.sqlite3"
    engine = SqliteEngine(db_file)
    store = SqliteDocumentStore(engine)
    repo = DurableHeatZoneCompositionRepository(store)
    zone_id = "MZ-1122334455667788"
    policy_id = f"heatzone-merge-v1:{TENANT_ID}"

    # 1. Save
    r1 = HeatZoneCompositionRecord(
        zone_id=zone_id,
        tenant_id=TENANT_ID,
        member_cell_id="cell-10",
        composition_kind=CompositionKind.MERGED,
        decision_policy_version_id=policy_id,
    )
    repo.save_composition(r1)

    assert repo.get_active_for_cell("cell-10", TENANT_ID) is not None

    # 2. Override
    repo.override_composition(
        zone_id=zone_id,
        tenant_id=TENANT_ID,
        decided_by="operator@odayplus.com",
        override_reason="Field survey override",
        decision_policy_version_id=policy_id,
    )

    lineage = repo.get_lineage(zone_id, TENANT_ID)
    assert lineage is not None
    assert lineage.decided_by == "operator@odayplus.com"

    # 3. Rollback
    repo.revert_composition(zone_id, TENANT_ID)
    post_lineage = repo.get_lineage(zone_id, TENANT_ID)
    assert post_lineage is not None
    assert post_lineage.is_active is False

