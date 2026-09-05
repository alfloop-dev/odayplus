"""Unit tests for HeatZone merge/split domain, readiness gate, and evaluation engine (ODP-FR-HZ-006)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from modules.heatzone.application.merge_split_engine import (
    MergeSplitPolicyError,
    MergeSplitReadinessInput,
    check_readiness_gates,
    derive_readiness_input,
    evaluate_merge_split,
)
from modules.heatzone.application.merge_split_evidence import (
    EvidenceUnavailableError,
    ExistingZoneComposition,
    assemble_merge_split_evidence,
    load_inventory_snapshot_facts,
)
from modules.heatzone.domain.composition import (
    COMPOSITION_MODEL_VERSION,
    CompositionKind,
    CompositionValidationError,
    HeatZoneCompositionRecord,
    MergeSplitProposalRecord,
    ProposalStatus,
    generate_merged_zone_id,
)
from modules.heatzone.infrastructure import (
    InMemoryMergeSplitEvidenceRepository,
)
from modules.heatzone.infrastructure.composition_repository import (
    InMemoryHeatZoneCompositionRepository,
)
from shared.governance import (
    DecisionPolicy,
    default_heatzone_merge_policy,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import (
    DurableHeatZoneCompositionRepository,
)
from tests.integration._heatzone_evidence import (
    ABSORPTION_POLICY_ID,
    MERGE_LEFT,
    MERGE_RIGHT,
    SPLIT_LEFT,
    SPLIT_RIGHT,
    add_barrier_evidence,
    build_evidence_repository,
    matured_receipt,
    tamper_eligible_count,
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


def _mature_evidence(tmp_path, *, with_barrier: bool = False, existing_zones=()):
    repository = build_evidence_repository()
    if with_barrier:
        add_barrier_evidence(repository)
    return assemble_merge_split_evidence(
        repository,
        tenant_id=TENANT_ID,
        existing_zones=existing_zones,
        receipt_path=matured_receipt(tmp_path / "inventory-receipt.json"),
    )


def test_production_inventory_receipt_reports_heatzone_governed_disabled() -> None:
    """The shipped snapshot is the one production reads, and it is not mature."""
    facts = load_inventory_snapshot_facts()

    assert facts.eligible_count == 0
    assert facts.eligible_count < facts.minimum_rows
    assert facts.governed_disabled is True
    assert facts.inventory_version.startswith("pg16-production-model-inventory-")


def test_evidence_refuses_a_receipt_whose_counts_were_edited(tmp_path) -> None:
    """Raising the label count without re-hashing must not unlock the gate."""
    receipt = matured_receipt(tmp_path / "receipt.json")
    assert load_inventory_snapshot_facts(receipt_path=receipt).eligible_count == 240

    tamper_eligible_count(receipt, eligible_count=9_000)

    with pytest.raises(EvidenceUnavailableError, match="not trustworthy"):
        load_inventory_snapshot_facts(receipt_path=receipt)


def test_evaluation_refuses_when_no_evidence_repository_is_wired() -> None:
    with pytest.raises(EvidenceUnavailableError, match="evidence repository"):
        assemble_merge_split_evidence(None, tenant_id=TENANT_ID)


def test_readiness_gate_fails_closed_on_the_production_snapshot() -> None:
    """Real history, real snapshot: still governed-disabled, still abstains."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    evidence = assemble_merge_split_evidence(
        build_evidence_repository(), tenant_id=TENANT_ID
    )

    readiness = derive_readiness_input(evidence, policy)
    assert readiness.governed_disabled is True
    assert readiness.mature_labels_count == 0

    result = check_readiness_gates(readiness, policy)
    assert result.eligible is False
    assert "governed_disabled_by_data_contract_maturity" in result.reasons
    assert any("sample_size_insufficient" in reason for reason in result.reasons)

    evaluation = evaluate_merge_split(evidence, policy=policy)
    assert evaluation.abstained is True
    assert evaluation.proposals == ()
    assert evaluation.abstain_reasons == result.reasons


def test_readiness_dimensions_are_measured_from_the_outcome_history(tmp_path) -> None:
    policy = default_heatzone_merge_policy(TENANT_ID)
    readiness = derive_readiness_input(_mature_evidence(tmp_path), policy)

    # 8 back-to-back 28-day periods; a horizon nobody declared.
    assert readiness.observation_days == 224
    assert readiness.metro_clusters_count == 2
    assert readiness.spatial_contiguity_ratio == 1.0
    assert readiness.adjacent_pairs_count == 30
    assert readiness.basis_source_id_count == 256
    assert readiness.absorption_ratio_cv is not None
    assert readiness.absorption_ratio_cv < 0.15
    assert readiness.drift_psi is not None and readiness.drift_psi < 0.10

    assert check_readiness_gates(readiness, policy).eligible is True


def test_readiness_reports_unmeasurable_stability_as_a_failure(tmp_path) -> None:
    """One period cannot support a CV, and an unmeasured CV is not a pass."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    evidence = _mature_evidence(tmp_path)
    single_period = MergeSplitReadinessInput(
        observation_days=224,
        mature_labels_count=240,
        active_store_count=106,
        adjacent_pairs_count=30,
        metro_clusters_count=2,
        spatial_contiguity_ratio=1.0,
        absorption_ratio_cv=None,
        drift_psi=None,
        wasserstein_distance=None,
        source_snapshot_id=evidence.snapshot.inventory_version,
        source_snapshot_sha256=evidence.snapshot.content_sha256,
        basis_source_id_count=8,
    )

    result = check_readiness_gates(single_period, policy)
    assert result.eligible is False
    assert "absorption_cv_unmeasured" in result.reasons
    assert "drift_psi_unmeasured" in result.reasons
    assert "wasserstein_distance_unmeasured" in result.reasons


def test_engine_refuses_a_policy_that_omits_a_threshold(tmp_path) -> None:
    """A missing threshold is a governance defect, not an invitation to guess."""
    base = default_heatzone_merge_policy(TENANT_ID)
    parameters = dict(base.parameters)
    del parameters["min_correlation_rho"]
    policy = DecisionPolicy(
        policy_version_id=base.policy_version_id,
        policy_label=base.policy_label,
        policy_id=base.policy_id,
        policy_version=base.policy_version,
        policy_kind=base.policy_kind,
        tenant_id=base.tenant_id,
        effective_from=base.effective_from,
        parameters=parameters,
        declared_inputs=base.declared_inputs,
    )

    with pytest.raises(MergeSplitPolicyError, match="min_correlation_rho"):
        evaluate_merge_split(_mature_evidence(tmp_path), policy=policy)


def test_merge_proposal_is_earned_from_measured_outcomes(tmp_path) -> None:
    policy = default_heatzone_merge_policy(TENANT_ID)
    evaluation = evaluate_merge_split(_mature_evidence(tmp_path), policy=policy)

    assert evaluation.abstained is False
    merges = [
        proposal
        for proposal in evaluation.proposals
        if proposal.composition_kind == CompositionKind.MERGED
    ]
    assert len(merges) == 1
    proposal = merges[0]

    assert proposal.member_cell_ids == (MERGE_LEFT, MERGE_RIGHT)
    assert proposal.correlation_rho >= 0.75
    assert proposal.disconnect_index <= 0.20
    assert proposal.ndcg_gain >= 0.05
    assert proposal.cannibalization_variance_reduction >= 0.20
    assert proposal.zone_id.startswith("MZ-")
    assert (
        f"source_snapshot:{evaluation.readiness.metrics['source_snapshot_id']}"
        in proposal.reasons
    )


def test_adjacent_cells_without_shared_trade_area_are_refused(tmp_path) -> None:
    """Adjacency alone proposes nothing; every other pair is declined on evidence."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    evaluation = evaluate_merge_split(_mature_evidence(tmp_path), policy=policy)

    declined = [d for d in evaluation.declined if d["kind"] == CompositionKind.MERGED.value]
    assert len(declined) == 29
    reasons = {entry["reason"].split(":")[0] for entry in declined}
    assert "correlation_below_threshold" in reasons
    assert "demand_disconnect_above_threshold" in reasons
    assert all(entry["paired_periods"] == 8 for entry in declined)


def test_split_requires_side_labelled_outcomes(tmp_path) -> None:
    """A zone with a barrier but no side evidence is declined, not guessed at."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    zone_id = generate_merged_zone_id((SPLIT_LEFT, SPLIT_RIGHT))
    zones = (ExistingZoneComposition(zone_id, "MERGED", (SPLIT_LEFT, SPLIT_RIGHT)),)

    without_sides = evaluate_merge_split(
        _mature_evidence(tmp_path, existing_zones=zones), policy=policy
    )
    assert [
        p for p in without_sides.proposals
        if p.composition_kind == CompositionKind.SPLIT_CHILD
    ] == []
    assert any(
        entry["reason"] == "no_side_labelled_hz004_outcomes_for_every_member_cell"
        for entry in without_sides.declined
    )

    with_sides = evaluate_merge_split(
        _mature_evidence(tmp_path, with_barrier=True, existing_zones=zones),
        policy=policy,
    )
    splits = [
        p for p in with_sides.proposals
        if p.composition_kind == CompositionKind.SPLIT_CHILD
    ]
    # One proposal for the whole division, not one per side: an operator
    # decides a topology, and half a topology is not a smaller decision.
    assert len(splits) == 1
    split = splits[0]
    # The parent is a zone that exists, so the lineage joins to a real row.
    assert split.parent_zone_id == zone_id
    assert split.split_density_ratio >= 2.5
    assert any("side_labelled_absorption_density_ratio" in r for r in split.reasons)
    assert set(split.child_partitions) == {(SPLIT_LEFT,), (SPLIT_RIGHT,)}
    assert set(split.member_cell_ids) == {SPLIT_LEFT, SPLIT_RIGHT}


def test_split_is_refused_when_the_sides_absorb_alike(tmp_path) -> None:
    policy = default_heatzone_merge_policy(TENANT_ID)
    zone_id = generate_merged_zone_id((SPLIT_LEFT, SPLIT_RIGHT))
    zones = (ExistingZoneComposition(zone_id, "MERGED", (SPLIT_LEFT, SPLIT_RIGHT)),)

    repository = build_evidence_repository()
    add_barrier_evidence(repository, heavy_side_multiple=1.1)
    evidence = assemble_merge_split_evidence(
        repository,
        tenant_id=TENANT_ID,
        existing_zones=zones,
        receipt_path=matured_receipt(tmp_path / "receipt.json"),
    )

    evaluation = evaluate_merge_split(evidence, policy=policy)
    assert [
        p for p in evaluation.proposals
        if p.composition_kind == CompositionKind.SPLIT_CHILD
    ] == []
    assert any(
        entry["reason"].startswith("side_density_ratio_below_threshold")
        for entry in evaluation.declined
    )


def test_split_is_refused_when_split_edge_is_missing_from_adjacency(tmp_path) -> None:
    """A mature fixture with side evidence but missing adjacency edge is refused."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    zone_id = generate_merged_zone_id((SPLIT_LEFT, SPLIT_RIGHT))
    zones = (ExistingZoneComposition(zone_id, "MERGED", (SPLIT_LEFT, SPLIT_RIGHT)),)

    repository = build_evidence_repository()
    add_barrier_evidence(repository)
    # Link an extra unrelated pair in Taipei so adjacent pair count remains >= 30
    repository.link_adjacent(TENANT_ID, "cell-taipei-00", "cell-taipei-02")
    # Remove the split candidate edge from adjacency while leaving 30 other pairs
    repository._adjacency[TENANT_ID] = {
        edge for edge in repository._adjacency.get(TENANT_ID, set())
        if edge != (SPLIT_LEFT, SPLIT_RIGHT) and edge != (SPLIT_RIGHT, SPLIT_LEFT)
    }

    evidence = assemble_merge_split_evidence(
        repository,
        tenant_id=TENANT_ID,
        existing_zones=zones,
        receipt_path=matured_receipt(tmp_path / "receipt.json"),
    )
    assert len(evidence.adjacency) >= 30  # 30 unrelated pairs remain

    evaluation = evaluate_merge_split(evidence, policy=policy)
    assert [
        p for p in evaluation.proposals
        if p.composition_kind == CompositionKind.SPLIT_CHILD
    ] == []
    assert any(
        entry["reason"] in (
            "split_partitions_not_spatially_adjacent",
            "split_candidate_zone_not_spatially_contiguous",
        )
        for entry in evaluation.declined
    )


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



def test_a_pair_already_in_one_zone_is_not_proposed_again(tmp_path) -> None:
    """Re-proposing a live zone would churn the audit trail for no change."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    zone_id = generate_merged_zone_id((MERGE_LEFT, MERGE_RIGHT))
    zones = (ExistingZoneComposition(zone_id, "MERGED", (MERGE_LEFT, MERGE_RIGHT)),)

    evaluation = evaluate_merge_split(
        _mature_evidence(tmp_path, existing_zones=zones), policy=policy
    )

    assert [
        proposal
        for proposal in evaluation.proposals
        if proposal.composition_kind == CompositionKind.MERGED
    ] == []
    assert any(
        entry["reason"] == f"already_composed_into_zone:{zone_id}"
        for entry in evaluation.declined
    )


def _populate_durable_sqlite_evidence(
    engine: SqliteEngine,
    reference: InMemoryMergeSplitEvidenceRepository,
    tenant_id: str = TENANT_ID,
) -> None:
    for cell in reference.list_cells(tenant_id):
        engine.execute(
            "INSERT INTO h3_cells (geo_cell_id, h3_index, centroid_latitude, "
            "centroid_longitude, admin_city, admin_district) "
            "VALUES (?, ?, 25.03, 121.56, ?, ?)",
            (cell.cell_id, cell.h3_index, cell.admin_city, cell.admin_district),
        )
    for index, outcome in enumerate(reference.list_absorption_outcomes(tenant_id)):
        engine.execute(
            """
            INSERT INTO heatzone_absorption_outcomes (
                outcome_id, tenant_id, geo_cell_id, period_start, period_end,
                original_demand, absorbed_demand, remaining_demand,
                absorption_ratio, absorbing_store_count, under_realized,
                barrier_side, barrier_description, basis_source_ids, basis_at,
                absorption_policy_version_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"outcome-{index}",
                tenant_id,
                outcome.cell_id,
                outcome.period_start.isoformat(),
                outcome.period_end.isoformat(),
                outcome.original_demand,
                outcome.absorbed_demand,
                outcome.remaining_demand,
                outcome.absorption_ratio,
                outcome.absorbing_store_count,
                int(outcome.under_realized),
                outcome.barrier_side,
                outcome.barrier_description,
                json.dumps(list(outcome.basis_source_ids)),
                outcome.basis_at.isoformat(),
                ABSORPTION_POLICY_ID,
                outcome.basis_at.isoformat(),
            ),
        )
    for index, (left, right) in enumerate(reference.list_adjacency(tenant_id)):
        engine.execute(
            "INSERT INTO h3_cell_adjacency (adjacency_id, cell_id, neighbor_cell_id, k_ring) "
            "VALUES (?, ?, ?, 1)",
            (f"edge-{index}", left, right),
        )


def test_durable_evidence_repository_reads_the_same_history(tmp_path) -> None:
    """The SQL reader must produce the evidence the engine acts on, not a shape
    that only the in-memory double satisfies."""
    from shared.infrastructure.persistence.repositories import (
        DurableMergeSplitEvidenceRepository,
    )
    from tests.integration._heatzone_evidence import build_evidence_repository

    engine = SqliteEngine(tmp_path / "evidence.sqlite3")
    durable = DurableMergeSplitEvidenceRepository(engine)
    reference = build_evidence_repository()
    _populate_durable_sqlite_evidence(engine, reference)

    assert durable.list_adjacency(TENANT_ID) == reference.list_adjacency(TENANT_ID)
    assert [c.cell_id for c in durable.list_cells(TENANT_ID)] == [
        c.cell_id for c in reference.list_cells(TENANT_ID)
    ]

    evidence = assemble_merge_split_evidence(
        durable,
        tenant_id=TENANT_ID,
        receipt_path=matured_receipt(tmp_path / "receipt.json"),
    )
    evaluation = evaluate_merge_split(
        evidence, policy=default_heatzone_merge_policy(TENANT_ID)
    )

    assert evaluation.abstained is False
    assert [
        tuple(p.member_cell_ids)
        for p in evaluation.proposals
        if p.composition_kind == CompositionKind.MERGED
    ] == [(MERGE_LEFT, MERGE_RIGHT)]


def test_durable_evidence_repository_drops_a_cell_the_geo_registry_lacks(
    tmp_path,
) -> None:
    """An unregistered cell has no admin identity, so the boundary rule cannot
    be applied to it and it must not reach the engine."""
    import json

    from shared.infrastructure.persistence.repositories import (
        DurableMergeSplitEvidenceRepository,
    )
    from tests.integration._heatzone_evidence import ABSORPTION_POLICY_ID

    engine = SqliteEngine(tmp_path / "orphan.sqlite3")
    durable = DurableMergeSplitEvidenceRepository(engine)
    engine.execute(
        """
        INSERT INTO heatzone_absorption_outcomes (
            outcome_id, tenant_id, geo_cell_id, period_start, period_end,
            original_demand, absorbed_demand, remaining_demand, absorption_ratio,
            absorbing_store_count, under_realized, barrier_side,
            barrier_description, basis_source_ids, basis_at,
            absorption_policy_version_id, created_at
        ) VALUES ('o-1', ?, 'cell-not-registered', '2026-01-05', '2026-02-01',
                  1000, 620, 380, 0.62, 3, 0, NULL, '', ?,
                  '2026-09-01T00:00:00+00:00', ?, '2026-09-01T00:00:00+00:00')
        """,
        (TENANT_ID, json.dumps(["fp-a"]), ABSORPTION_POLICY_ID),
    )

    assert len(durable.list_absorption_outcomes(TENANT_ID)) == 1
    assert durable.list_cells(TENANT_ID) == []


def test_durable_evidence_repository_refuses_split_when_cells_lack_barrier_sides(
    tmp_path,
) -> None:
    """On durable evidence repository where cells lack geo barrier sides,
    the split engine explicitly declines split candidates with the named rule
    'no_side_labelled_hz004_outcomes_for_every_member_cell'."""
    from shared.infrastructure.persistence.repositories import (
        DurableMergeSplitEvidenceRepository,
    )
    from tests.integration._heatzone_evidence import build_evidence_repository

    engine = SqliteEngine(tmp_path / "durable_split.sqlite3")
    durable = DurableMergeSplitEvidenceRepository(engine)
    reference = build_evidence_repository()
    _populate_durable_sqlite_evidence(engine, reference)

    zone_id = generate_merged_zone_id([SPLIT_LEFT, SPLIT_RIGHT])
    existing = [
        ExistingZoneComposition(
            zone_id=zone_id,
            composition_kind=CompositionKind.MERGED,
            member_cell_ids=(SPLIT_LEFT, SPLIT_RIGHT),
        )
    ]
    evidence = assemble_merge_split_evidence(
        durable,
        tenant_id=TENANT_ID,
        receipt_path=matured_receipt(tmp_path / "receipt.json"),
        existing_zones=existing,
    )
    evaluation = evaluate_merge_split(
        evidence, policy=default_heatzone_merge_policy(TENANT_ID)
    )
    assert not any(p.composition_kind == CompositionKind.SPLIT_CHILD for p in evaluation.proposals)
    assert any(
        d.get("reason") == "no_side_labelled_hz004_outcomes_for_every_member_cell"
        for d in evaluation.declined
    )


def test_durable_evidence_repository_calculates_true_spatial_contiguity_and_abstains_when_incomplete(
    tmp_path,
) -> None:
    """When adjacent neighbor cells in the authoritative graph lack absorption outcomes,
    spatial_contiguity_ratio drops below the threshold and evaluation abstains fail-closed."""
    from shared.infrastructure.persistence.repositories import (
        DurableMergeSplitEvidenceRepository,
    )
    from tests.integration._heatzone_evidence import build_evidence_repository

    engine = SqliteEngine(tmp_path / "contiguity.sqlite3")
    durable = DurableMergeSplitEvidenceRepository(engine)
    reference = build_evidence_repository()
    _populate_durable_sqlite_evidence(engine, reference)

    # Insert 10 additional adjacent cells in the geographic registry connected to MERGE_LEFT
    # but with NO outcomes recorded for them (30 / 40 = 0.75 < 0.80 threshold).
    unobserved_neighbors = [f"cell-neighbor-{i}" for i in range(10)]
    for i, cell_id in enumerate(unobserved_neighbors):
        engine.execute(
            "INSERT INTO h3_cells (geo_cell_id, h3_index, centroid_latitude, "
            "centroid_longitude, admin_city, admin_district) "
            "VALUES (?, ?, 25.03, 121.56, 'Taipei', 'Xinyi')",
            (cell_id, f"894ba0a4e7{i:02d}ffff"),
        )
        left, right = min(MERGE_LEFT, cell_id), max(MERGE_LEFT, cell_id)
        engine.execute(
            "INSERT INTO h3_cell_adjacency (adjacency_id, cell_id, neighbor_cell_id, k_ring) "
            "VALUES (?, ?, ?, 1)",
            (f"extra-edge-{i}", left, right),
        )

    # The adjacency query should include the extra edges connected to MERGE_LEFT
    adj = durable.list_adjacency(TENANT_ID)
    for neighbor in unobserved_neighbors:
        pair = (min(MERGE_LEFT, neighbor), max(MERGE_LEFT, neighbor))
        assert pair in adj

    evidence = assemble_merge_split_evidence(
        durable,
        tenant_id=TENANT_ID,
        receipt_path=matured_receipt(tmp_path / "receipt.json"),
    )

    policy = default_heatzone_merge_policy(TENANT_ID)
    evaluation = evaluate_merge_split(evidence, policy=policy)

    # Total graph cells = cells from reference + 10 unobserved neighbors
    # Since unobserved neighbors have no outcomes, spatial_contiguity_ratio drops significantly below 0.80
    assert evaluation.readiness.eligible is False
    assert evaluation.readiness.metrics["spatial_contiguity_ratio"] < 0.80
    assert evaluation.abstained is True
    assert evaluation.proposals == ()
    assert any("spatial_contiguity_insufficient" in r for r in evaluation.abstain_reasons)


def test_counterfactual_gates_bind_when_their_signal_is_removed(tmp_path) -> None:
    """Take away one signal at a time and the matching rule refuses the pair.

    Both statistics could otherwise be read as decoration. Removing the zone's
    store-count movement leaves the dilution fit unidentified; flattening the
    pair into two persistently unequal cells makes pooling actively worse and
    drives the NDCG gain negative. Neither variant merges.
    """
    policy = default_heatzone_merge_policy(TENANT_ID)
    receipt = matured_receipt(tmp_path / "receipt.json")

    def _decline(pair_store_counts):
        evidence = assemble_merge_split_evidence(
            build_evidence_repository(pair_store_counts=pair_store_counts),
            tenant_id=TENANT_ID,
            receipt_path=receipt,
        )
        evaluation = evaluate_merge_split(evidence, policy=policy)
        assert [
            proposal
            for proposal in evaluation.proposals
            if proposal.composition_kind == CompositionKind.MERGED
        ] == []
        return next(
            entry["reason"]
            for entry in evaluation.declined
            if entry["candidate"] == f"{MERGE_LEFT}+{MERGE_RIGHT}"
        )

    # The pair total never moves, so "does the zone's store count explain this
    # cell's take better than its own?" has no answer to give.
    assert (
        _decline(((2, 6, 2, 6, 2, 6, 2, 6), (6, 2, 6, 2, 6, 2, 6, 2)))
        == "cannibalization_variance_reduction_unmeasurable"
    )

    # Persistently unequal cells: pooling them hides a real difference, and the
    # ranking gets worse rather than merely failing to improve.
    ndcg_reason = _decline(((2,) * 8, (10,) * 8))
    assert ndcg_reason.startswith("ndcg_gain_below_threshold")
    assert float(ndcg_reason.split(":")[1].split("<")[0]) < 0.0


# ---------------------------------------------------------------------------
# Split approval atomicity (ODP-FR-HZ-006)
#
# A split retires the parent zone. Anything the approval fails to re-home is
# therefore left in no active zone at all, and no later approval can repair it
# because the parent it would have to be split from is gone. These tests hold
# the two halves of that rule: the whole division has to be on one proposal,
# and applying it has to be all-or-nothing.
# ---------------------------------------------------------------------------

SPLIT_CELLS = ("cell-split-00", "cell-split-01", "cell-split-02", "cell-split-03")


def _split_proposal(
    *,
    proposal_id: str = "split-proposal",
    member_cell_ids: tuple[str, ...] = SPLIT_CELLS,
    child_partitions: tuple[tuple[str, ...], ...] = (
        SPLIT_CELLS[:2],
        SPLIT_CELLS[2:],
    ),
    parent_zone_id: str | None = None,
) -> MergeSplitProposalRecord:
    parent = parent_zone_id or generate_merged_zone_id(SPLIT_CELLS)
    return MergeSplitProposalRecord(
        proposal_id=proposal_id,
        zone_id=parent,
        tenant_id=TENANT_ID,
        composition_kind=CompositionKind.SPLIT_CHILD,
        member_cell_ids=member_cell_ids,
        parent_zone_id=parent,
        child_partitions=child_partitions,
        ndcg_gain=0.0,
        cannibalization_variance_reduction=0.0,
        correlation_rho=0.10,
        disconnect_index=0.55,
        split_density_ratio=3.2,
        confidence=0.64,
        model_version=COMPOSITION_MODEL_VERSION,
        policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
    )


def _seed_parent_zone(repository, *, cells: tuple[str, ...] = SPLIT_CELLS) -> str:
    zone_id = generate_merged_zone_id(cells)
    for cell_id in cells:
        repository.save_composition(
            HeatZoneCompositionRecord(
                zone_id=zone_id,
                tenant_id=TENANT_ID,
                member_cell_id=cell_id,
                composition_kind=CompositionKind.MERGED,
                decided_by="system",
                decision_policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
            )
        )
    return zone_id


def _durable_composition_repository(tmp_path) -> DurableHeatZoneCompositionRepository:
    engine = SqliteEngine(str(tmp_path / "composition.db"))
    SqliteDocumentStore(engine)
    return DurableHeatZoneCompositionRepository(engine)


@pytest.mark.parametrize(
    ("child_partitions", "member_cell_ids", "expected"),
    [
        # One side only: approving it would retire the parent and strand the rest.
        ((SPLIT_CELLS[:2],), SPLIT_CELLS, "at least 2"),
        # A cell in both children: it would need two active memberships.
        (
            (SPLIT_CELLS[:3], SPLIT_CELLS[2:]),
            SPLIT_CELLS,
            "more than one child partition",
        ),
        # A member nobody re-homes.
        ((SPLIT_CELLS[:2], (SPLIT_CELLS[2],)), SPLIT_CELLS, "do not cover its members"),
        # A child cell that is not a member of the zone being divided.
        (
            (SPLIT_CELLS[:2], (SPLIT_CELLS[2], "cell-elsewhere")),
            SPLIT_CELLS,
            "do not cover its members",
        ),
        # An empty side is not a child.
        (((), SPLIT_CELLS), SPLIT_CELLS, "is empty"),
    ],
)
def test_a_split_proposal_must_describe_the_whole_division(
    child_partitions, member_cell_ids, expected
) -> None:
    with pytest.raises(CompositionValidationError, match=expected):
        _split_proposal(
            child_partitions=child_partitions, member_cell_ids=member_cell_ids
        )


def test_a_merge_proposal_must_not_carry_child_partitions() -> None:
    with pytest.raises(CompositionValidationError, match="must not carry child_partitions"):
        MergeSplitProposalRecord(
            proposal_id="merge-proposal",
            zone_id=generate_merged_zone_id(SPLIT_CELLS[:2]),
            tenant_id=TENANT_ID,
            composition_kind=CompositionKind.MERGED,
            member_cell_ids=SPLIT_CELLS[:2],
            parent_zone_id=None,
            child_partitions=(SPLIT_CELLS[:1], SPLIT_CELLS[1:2]),
            ndcg_gain=0.06,
            cannibalization_variance_reduction=0.25,
            correlation_rho=0.80,
            disconnect_index=0.10,
            confidence=0.70,
            model_version=COMPOSITION_MODEL_VERSION,
            policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
        )


@pytest.mark.parametrize("durable", [False, True])
def test_approving_a_split_lands_every_child_and_retires_the_parent(
    tmp_path, durable
) -> None:
    """One approval, one complete topology -- no cell left outside a zone."""
    repository = (
        _durable_composition_repository(tmp_path)
        if durable
        else InMemoryHeatZoneCompositionRepository()
    )
    parent_zone_id = _seed_parent_zone(repository)
    repository.save_proposal(_split_proposal())

    updated, created = repository.approve_proposal(
        proposal_id="split-proposal",
        tenant_id=TENANT_ID,
        approved_by="operator-a",
        notes="split on the recorded barrier",
    )

    assert updated.status is ProposalStatus.APPROVED
    # Both children exist, each with its own deterministic zone id.
    assert {record.zone_id for record in created} == {
        generate_merged_zone_id(SPLIT_CELLS[:2]),
        generate_merged_zone_id(SPLIT_CELLS[2:]),
    }
    assert all(record.parent_zone_id == parent_zone_id for record in created)

    # The parent is retired and every one of its cells is active somewhere else.
    parent_records = repository.get_composition(parent_zone_id, TENANT_ID)
    assert not any(record.is_active for record in parent_records)
    active = repository.list_compositions(TENANT_ID, active_only=True)
    assert sorted(record.member_cell_id for record in active) == sorted(SPLIT_CELLS)
    assert all(
        record.composition_kind is CompositionKind.SPLIT_CHILD for record in active
    )

    # And the whole division is one decision, so there is nothing left to approve.
    assert repository.list_proposals(TENANT_ID, status=ProposalStatus.PROPOSED) == []


@pytest.mark.parametrize("durable", [False, True])
def test_a_split_approval_that_cannot_finish_applies_nothing(tmp_path, durable) -> None:
    """A child that cannot be written rolls the whole approval back.

    The failure is injected on the second child because that is the ordering
    that matters: by then the parent has been reverted and the first child
    written, so a non-atomic approval would leave the parent retired, one child
    live, and the other side's cells in no active zone -- with no parent left to
    split them from, which is the state no later approval can repair.
    """
    repository = (
        _durable_composition_repository(tmp_path)
        if durable
        else InMemoryHeatZoneCompositionRepository()
    )
    parent_zone_id = _seed_parent_zone(repository)
    repository.save_proposal(_split_proposal())

    first_child_zone_id = generate_merged_zone_id(SPLIT_CELLS[:2])
    second_child_zone_id = generate_merged_zone_id(SPLIT_CELLS[2:])
    real_save = repository.save_composition

    def failing_save(record):
        if record.zone_id == second_child_zone_id:
            raise CompositionValidationError("injected storage failure")
        return real_save(record)

    repository.save_composition = failing_save

    with pytest.raises(CompositionValidationError, match="injected storage failure"):
        repository.approve_proposal(
            proposal_id="split-proposal",
            tenant_id=TENANT_ID,
            approved_by="operator-a",
            notes="split on the recorded barrier",
        )

    repository.save_composition = real_save

    # Neither child landed -- not even the one that was written before the
    # failure -- and the parent still holds every cell.
    assert repository.get_composition(first_child_zone_id, TENANT_ID) == []
    assert repository.get_composition(second_child_zone_id, TENANT_ID) == []
    parent_records = repository.get_composition(parent_zone_id, TENANT_ID)
    assert sorted(r.member_cell_id for r in parent_records if r.is_active) == sorted(
        SPLIT_CELLS
    )
    active = repository.list_compositions(TENANT_ID, active_only=True)
    assert {r.zone_id for r in active} == {parent_zone_id}

    # The proposal is still open, so the operator can act once storage recovers.
    still_open = repository.list_proposals(TENANT_ID, status=ProposalStatus.PROPOSED)
    assert [p.proposal_id for p in still_open] == ["split-proposal"]


@pytest.mark.parametrize("durable", [False, True])
def test_a_split_survives_persistence_and_rejection(tmp_path, durable) -> None:
    """The partitions are part of the record, not a detail of the request."""
    repository = (
        _durable_composition_repository(tmp_path)
        if durable
        else InMemoryHeatZoneCompositionRepository()
    )
    _seed_parent_zone(repository)
    repository.save_proposal(_split_proposal())

    reloaded = repository.get_proposal("split-proposal", TENANT_ID)
    assert reloaded is not None
    assert reloaded.child_partitions == (SPLIT_CELLS[:2], SPLIT_CELLS[2:])
    assert reloaded.to_dict()["child_zone_ids"] == [
        generate_merged_zone_id(SPLIT_CELLS[:2]),
        generate_merged_zone_id(SPLIT_CELLS[2:]),
    ]

    rejected = repository.reject_proposal(
        proposal_id="split-proposal",
        tenant_id=TENANT_ID,
        rejected_by="operator-a",
        reason="the barrier is a temporary roadworks closure",
    )
    assert rejected.status is ProposalStatus.REJECTED
    # Rejecting a split must not silently drop the division it described.
    assert rejected.child_partitions == (SPLIT_CELLS[:2], SPLIT_CELLS[2:])


@pytest.mark.parametrize("durable", [False, True])
def test_a_split_child_can_be_rolled_back_to_nothing_active(tmp_path, durable) -> None:
    """Rollback of an approved child leaves its cells claimed by no zone.

    That is the honest post-rollback state: the parent was retired by an
    append-only revert and cannot be un-reverted, so restoring the previous
    topology is a new composition decision rather than something rollback can
    infer.
    """
    repository = (
        _durable_composition_repository(tmp_path)
        if durable
        else InMemoryHeatZoneCompositionRepository()
    )
    _seed_parent_zone(repository)
    repository.save_proposal(_split_proposal())
    repository.approve_proposal(
        proposal_id="split-proposal",
        tenant_id=TENANT_ID,
        approved_by="operator-a",
        notes="split on the recorded barrier",
    )

    child_zone_id = generate_merged_zone_id(SPLIT_CELLS[:2])
    reverted = repository.revert_composition(child_zone_id, TENANT_ID)
    assert sorted(r.member_cell_id for r in reverted) == sorted(SPLIT_CELLS[:2])
    assert all(not r.is_active for r in reverted)

    active_cells = {
        r.member_cell_id
        for r in repository.list_compositions(TENANT_ID, active_only=True)
    }
    assert active_cells == set(SPLIT_CELLS[2:])
    # Reverting twice is refused rather than quietly appending a second revert.
    with pytest.raises(CompositionValidationError):
        repository.revert_composition(child_zone_id, TENANT_ID)


@pytest.mark.parametrize("durable", [False, True])
def test_merge_approval_rejects_partial_replacement_of_active_multi_cell_zone(
    tmp_path, durable
) -> None:
    """Approval must fail closed if retiring a zone leaves sibling cells stranded."""
    repository = (
        _durable_composition_repository(tmp_path)
        if durable
        else InMemoryHeatZoneCompositionRepository()
    )
    multi_cells = ("cell-mc-a", "cell-mc-b", "cell-mc-c")
    parent_zone_id = _seed_parent_zone(repository, cells=multi_cells)

    merge_proposal = MergeSplitProposalRecord(
        proposal_id="partial-merge-proposal",
        zone_id=generate_merged_zone_id(("cell-mc-a", "cell-mc-x")),
        tenant_id=TENANT_ID,
        composition_kind=CompositionKind.MERGED,
        member_cell_ids=("cell-mc-a", "cell-mc-x"),
        parent_zone_id=None,
        ndcg_gain=0.08,
        cannibalization_variance_reduction=0.30,
        correlation_rho=0.85,
        disconnect_index=0.05,
        confidence=0.80,
        model_version=COMPOSITION_MODEL_VERSION,
        policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
    )
    repository.save_proposal(merge_proposal)

    with pytest.raises(CompositionValidationError, match="partial replacement of active zone"):
        repository.approve_proposal(
            proposal_id="partial-merge-proposal",
            tenant_id=TENANT_ID,
            approved_by="operator-a",
            notes="attempted partial merge",
        )

    # All 3 original cells remain active in the original zone
    parent_records = repository.get_composition(parent_zone_id, TENANT_ID)
    assert len(parent_records) == 3
    assert all(r.is_active for r in parent_records)
    assert sorted(r.member_cell_id for r in parent_records) == sorted(multi_cells)

    active = repository.list_compositions(TENANT_ID, active_only=True)
    assert len(active) == 3
    assert {r.zone_id for r in active} == {parent_zone_id}

    # Proposal remains PROPOSED
    prop = repository.get_proposal("partial-merge-proposal", TENANT_ID)
    assert prop is not None
    assert prop.status == ProposalStatus.PROPOSED


@pytest.mark.parametrize("durable", [False, True])
def test_merge_approval_succeeds_when_all_sibling_cells_of_active_zones_are_covered(
    tmp_path, durable
) -> None:
    """Approval succeeds when the proposal covers all members of all touched active zones."""
    repository = (
        _durable_composition_repository(tmp_path)
        if durable
        else InMemoryHeatZoneCompositionRepository()
    )
    zone1_cells = ("cell-z1-a", "cell-z1-b")
    zone2_cells = ("cell-z2-x", "cell-z2-y")
    zone1_id = _seed_parent_zone(repository, cells=zone1_cells)
    zone2_id = _seed_parent_zone(repository, cells=zone2_cells)

    all_cells = (*zone1_cells, *zone2_cells)
    merged_zone_id = generate_merged_zone_id(all_cells)
    full_merge_proposal = MergeSplitProposalRecord(
        proposal_id="full-merge-proposal",
        zone_id=merged_zone_id,
        tenant_id=TENANT_ID,
        composition_kind=CompositionKind.MERGED,
        member_cell_ids=all_cells,
        parent_zone_id=None,
        ndcg_gain=0.10,
        cannibalization_variance_reduction=0.35,
        correlation_rho=0.88,
        disconnect_index=0.04,
        confidence=0.85,
        model_version=COMPOSITION_MODEL_VERSION,
        policy_version_id=f"heatzone-merge-v1:{TENANT_ID}",
    )
    repository.save_proposal(full_merge_proposal)

    updated, created = repository.approve_proposal(
        proposal_id="full-merge-proposal",
        tenant_id=TENANT_ID,
        approved_by="operator-a",
        notes="full merge of both zones",
    )

    assert updated.status == ProposalStatus.APPROVED
    assert len(created) == 4
    assert all(r.zone_id == merged_zone_id for r in created)

    # Both old zones are reverted
    z1_records = repository.get_composition(zone1_id, TENANT_ID)
    assert not any(r.is_active for r in z1_records)
    z2_records = repository.get_composition(zone2_id, TENANT_ID)
    assert not any(r.is_active for r in z2_records)

    # All 4 cells are active under the new merged zone
    active = repository.list_compositions(TENANT_ID, active_only=True)
    assert len(active) == 4
    assert sorted(r.member_cell_id for r in active) == sorted(all_cells)
    assert all(r.zone_id == merged_zone_id for r in active)


def test_merge_candidate_declined_if_cell_is_part_of_multi_cell_zone(tmp_path) -> None:
    """Merge engine declines candidates that would partially replace a multi-cell zone."""
    policy = default_heatzone_merge_policy(TENANT_ID)
    zone_id = generate_merged_zone_id((MERGE_LEFT, "cell-extra-sibling"))
    zones = (
        ExistingZoneComposition(
            zone_id, "MERGED", (MERGE_LEFT, "cell-extra-sibling")
        ),
    )

    evaluation = evaluate_merge_split(
        _mature_evidence(tmp_path, existing_zones=zones), policy=policy
    )

    # No merge proposal for MERGE_LEFT + MERGE_RIGHT because MERGE_LEFT is in a multi-cell zone
    assert not any(
        MERGE_LEFT in p.member_cell_ids and p.composition_kind == CompositionKind.MERGED
        for p in evaluation.proposals
    )
    assert any(
        entry.get("reason") == f"partial_replacement_of_multi_cell_zone:{zone_id}"
        for entry in evaluation.declined
    )
