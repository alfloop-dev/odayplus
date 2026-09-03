"""Unit tests for HeatZone merge/split domain, readiness gate, and evaluation engine (ODP-FR-HZ-006)."""

from __future__ import annotations

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
    CompositionKind,
    CompositionValidationError,
    HeatZoneCompositionRecord,
    generate_merged_zone_id,
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
    children = [
        p for p in with_sides.proposals
        if p.composition_kind == CompositionKind.SPLIT_CHILD
    ]
    assert len(children) == 2
    for child in children:
        # The parent is a zone that exists, so the lineage joins to a real row.
        assert child.parent_zone_id == zone_id
        assert child.split_density_ratio >= 2.5
        assert any("side_labelled_absorption_density_ratio" in r for r in child.reasons)
    assert {tuple(child.member_cell_ids) for child in children} == {
        (SPLIT_LEFT,),
        (SPLIT_RIGHT,),
    }


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


def test_durable_evidence_repository_reads_the_same_history(tmp_path) -> None:
    """The SQL reader must produce the evidence the engine acts on, not a shape
    that only the in-memory double satisfies."""
    import json

    from shared.infrastructure.persistence.repositories import (
        DurableMergeSplitEvidenceRepository,
    )
    from tests.integration._heatzone_evidence import (
        ABSORPTION_POLICY_ID,
        build_evidence_repository,
    )

    engine = SqliteEngine(tmp_path / "evidence.sqlite3")
    durable = DurableMergeSplitEvidenceRepository(engine)
    reference = build_evidence_repository()

    for cell in reference.list_cells(TENANT_ID):
        engine.execute(
            "INSERT INTO h3_cells (geo_cell_id, h3_index, centroid_latitude, "
            "centroid_longitude, admin_city, admin_district) "
            "VALUES (?, ?, 25.03, 121.56, ?, ?)",
            (cell.cell_id, cell.h3_index, cell.admin_city, cell.admin_district),
        )
    for index, outcome in enumerate(reference.list_absorption_outcomes(TENANT_ID)):
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
                TENANT_ID,
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
    for index, (left, right) in enumerate(reference.list_adjacency(TENANT_ID)):
        engine.execute(
            "INSERT INTO h3_cell_adjacency (adjacency_id, cell_id, neighbor_cell_id, k_ring) "
            "VALUES (?, ?, ?, 1)",
            (f"edge-{index}", left, right),
        )

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
