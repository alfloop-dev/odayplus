"""HeatZone merge/split evaluation and readiness engine (ODP-FR-HZ-006).

Every number this engine acts on is measured from realised HZ-004 absorption
history assembled by `merge_split_evidence`. Nothing is estimated from a
closed-form of the threshold it is later compared against, and nothing is read
off the request: geometry only ever *narrows* the candidate set, and a candidate
that survives geometry still has to earn its proposal from outcomes.

Two rules follow from that, and they are what make the engine abstain rather
than guess:

* A statistic that cannot be computed is `None`, never a default. A pair with
  too few jointly-observed periods yields no correlation, and therefore no
  proposal -- as opposed to a correlation of 0.0, which would merely lose on the
  threshold and could be nudged back over it by unrelated inputs.
* Thresholds are read from the governing `DecisionPolicy` with no code
  fallbacks. A policy that omits a threshold is a governance defect, and the
  engine refuses instead of substituting a number nobody approved.

The counterfactual statistics (`ndcg_gain`,
`cannibalization_variance_reduction`) are computed by replaying the observed
periods, so a merge that does not actually improve ranking or explain
cannibalization produces a negative gain and is dropped.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from modules.heatzone.application.merge_split_evidence import (
    CellOutcomeSeries,
    MergeSplitEvidence,
    aligned_periods,
    coefficient_of_variation,
    contiguous_observation_days,
    count_eligible_pairs,
    population_stability_index,
    spatial_contiguity_ratio,
    wasserstein_distance_1d,
)
from modules.heatzone.domain.composition import (
    COMPOSITION_MODEL_VERSION,
    CompositionKind,
    MergeSplitProposalRecord,
    generate_merged_zone_id,
)
from shared.governance import DecisionPolicy

#: Policy keys the engine requires. Absence is a governance defect, not a
#: prompt to fall back to a literal.
READINESS_POLICY_KEYS = (
    "min_observation_days",
    "min_mature_labels",
    "min_active_stores",
    "min_adjacent_pairs",
    "min_metro_clusters",
    "min_spatial_contiguity",
    "max_absorption_cv",
    "max_drift_psi",
    "max_wasserstein",
    "min_paired_periods",
)

DECISION_POLICY_KEYS = (
    "min_correlation_rho",
    "max_disconnect_index",
    "min_split_density_ratio",
    "min_ndcg_gain",
    "min_cannibalization_variance_reduction",
    "min_paired_periods",
    "min_split_side_periods",
    "allow_cross_admin_boundary",
)


class MergeSplitPolicyError(ValueError):
    """Raised when the governing policy omits a threshold the engine needs."""


@dataclass(frozen=True)
class MergeSplitReadinessInput:
    """Measured production maturity, derived only from trusted evidence.

    Constructed by `derive_readiness_input`; there is deliberately no path that
    builds one from an API payload.
    """

    observation_days: int = 0
    mature_labels_count: int = 0
    active_store_count: int = 0
    adjacent_pairs_count: int = 0
    metro_clusters_count: int = 0
    spatial_contiguity_ratio: float = 0.0
    absorption_ratio_cv: float | None = None
    drift_psi: float | None = None
    wasserstein_distance: float | None = None
    is_synthetic: bool = False
    governed_disabled: bool = False
    source_snapshot_id: str = ""
    source_snapshot_sha256: str = ""
    outcome_period_count: int = 0
    basis_source_id_count: int = 0


@dataclass(frozen=True)
class MergeSplitReadinessResult:
    """Outcome of the four-dimension empirical readiness gate."""

    eligible: bool
    reasons: tuple[str, ...]
    metrics: dict[str, Any]
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "metrics": self.metrics,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class PairStatistics:
    """Measured relationship between two adjacent cells over shared periods."""

    paired_periods: int
    correlation_rho: float | None
    disconnect_index: float | None
    ndcg_gain: float | None
    variance_reduction: float | None


@dataclass(frozen=True)
class MergeSplitProposal:
    """Proposed merge or split, carrying the statistics that justified it."""

    proposal_id: str
    zone_id: str
    tenant_id: str
    composition_kind: CompositionKind
    member_cell_ids: tuple[str, ...]
    parent_zone_id: str | None
    ndcg_gain: float
    cannibalization_variance_reduction: float
    correlation_rho: float
    disconnect_index: float
    confidence: float
    model_version: str
    policy_version_id: str
    split_density_ratio: float | None = None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "zone_id": self.zone_id,
            "tenant_id": self.tenant_id,
            "composition_kind": self.composition_kind.value,
            "member_cell_ids": list(self.member_cell_ids),
            "member_count": len(self.member_cell_ids),
            "parent_zone_id": self.parent_zone_id,
            "ndcg_gain": self.ndcg_gain,
            "cannibalization_variance_reduction": self.cannibalization_variance_reduction,
            "correlation_rho": self.correlation_rho,
            "disconnect_index": self.disconnect_index,
            "split_density_ratio": self.split_density_ratio,
            "confidence": self.confidence,
            "model_version": self.model_version,
            "policy_version_id": self.policy_version_id,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }

    def to_record(self) -> MergeSplitProposalRecord:
        return MergeSplitProposalRecord(
            proposal_id=self.proposal_id,
            zone_id=self.zone_id,
            tenant_id=self.tenant_id,
            composition_kind=self.composition_kind,
            member_cell_ids=self.member_cell_ids,
            parent_zone_id=self.parent_zone_id,
            ndcg_gain=self.ndcg_gain,
            cannibalization_variance_reduction=self.cannibalization_variance_reduction,
            correlation_rho=self.correlation_rho,
            disconnect_index=self.disconnect_index,
            split_density_ratio=self.split_density_ratio,
            confidence=self.confidence,
            model_version=self.model_version,
            policy_version_id=self.policy_version_id,
            reasons=self.reasons,
            warnings=self.warnings,
        )


@dataclass(frozen=True)
class MergeSplitEvaluationResult:
    """Readiness ruling plus whatever survived the outcome tests."""

    tenant_id: str
    abstained: bool
    abstain_reasons: tuple[str, ...]
    readiness: MergeSplitReadinessResult
    proposals: tuple[MergeSplitProposal, ...]
    evaluated_at: datetime
    model_version: str
    policy_version_id: str
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    declined: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "abstained": self.abstained,
            "abstain_reasons": list(self.abstain_reasons),
            "readiness": self.readiness.to_dict(),
            "proposals": [p.to_dict() for p in self.proposals],
            "proposal_count": len(self.proposals),
            "declined_candidates": [dict(d) for d in self.declined],
            "evidence": dict(self.evidence_summary),
            "evaluated_at": self.evaluated_at.isoformat(),
            "model_version": self.model_version,
            "policy_version_id": self.policy_version_id,
        }


def _require(policy: DecisionPolicy, key: str) -> Any:
    value = policy.parameters.get(key)
    if value is None:
        raise MergeSplitPolicyError(
            f"policy {policy.policy_version_id} declares no {key}; heat-zone "
            "merge/split thresholds are governed values and have no default here"
        )
    return value


def _require_int(policy: DecisionPolicy, key: str) -> int:
    raw = _require(policy, key)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise MergeSplitPolicyError(
            f"policy {policy.policy_version_id}: {key}={raw!r} is not an integer"
        ) from exc


def _require_float(policy: DecisionPolicy, key: str) -> float:
    raw = _require(policy, key)
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise MergeSplitPolicyError(
            f"policy {policy.policy_version_id}: {key}={raw!r} is not a number"
        ) from exc


def _require_bool(policy: DecisionPolicy, key: str) -> bool:
    raw = policy.parameters.get(key)
    if raw is None:
        raise MergeSplitPolicyError(
            f"policy {policy.policy_version_id} declares no {key}; heat-zone "
            "merge/split thresholds are governed values and have no default here"
        )
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        if raw.strip().lower() in ("true", "1", "yes"):
            return True
        if raw.strip().lower() in ("false", "0", "no"):
            return False
    if isinstance(raw, (int, float)):
        return bool(raw)
    raise MergeSplitPolicyError(
        f"policy {policy.policy_version_id}: {key}={raw!r} is not a boolean"
    )


def derive_readiness_input(
    evidence: MergeSplitEvidence, policy: DecisionPolicy
) -> MergeSplitReadinessInput:
    """Measure the four readiness dimensions from trusted evidence.

    Nothing here is declared by a caller: counts come from the integrity-checked
    inventory receipt, and horizon, coverage and stability are computed from the
    persisted outcome series.
    """
    min_paired_periods = _require_int(policy, "min_paired_periods")
    cell_map = evidence.cell_map()
    snapshot = evidence.snapshot

    ratios: list[float] = []
    periods: list[tuple[date, date]] = []
    basis_ids: set[str] = set()
    active_stores = 0
    for cell in evidence.cells:
        if not cell.outcomes:
            continue
        # Cells are disjoint spatial units, so a store is counted once by the
        # cell that contains it; the peak observed count is that cell's
        # contribution to the absorbing-store population.
        active_stores += max(o.absorbing_store_count for o in cell.outcomes)
        for outcome in cell.outcomes:
            ratios.append(outcome.absorption_ratio)
            periods.append(outcome.period)
            basis_ids.update(outcome.basis_source_ids)

    ordered_ratios = [ratio for _, ratio in sorted(
        ((outcome.period, outcome.absorption_ratio)
         for cell in evidence.cells
         for outcome in cell.outcomes),
        key=lambda item: item[0],
    )]
    midpoint = len(ordered_ratios) // 2
    earlier, later = ordered_ratios[:midpoint], ordered_ratios[midpoint:]

    metro_clusters = {
        cell.admin_city.strip()
        for cell in evidence.cells
        if cell.outcomes and cell.admin_city.strip()
    }

    return MergeSplitReadinessInput(
        observation_days=contiguous_observation_days(evidence.cells),
        mature_labels_count=snapshot.eligible_count,
        active_store_count=active_stores,
        adjacent_pairs_count=count_eligible_pairs(
            evidence.adjacency, cell_map, min_paired_periods=min_paired_periods
        ),
        metro_clusters_count=len(metro_clusters),
        spatial_contiguity_ratio=spatial_contiguity_ratio(evidence.adjacency, cell_map),
        absorption_ratio_cv=coefficient_of_variation(ratios),
        drift_psi=population_stability_index(earlier, later),
        wasserstein_distance=wasserstein_distance_1d(earlier, later),
        is_synthetic=snapshot.auto_seeded,
        governed_disabled=snapshot.governed_disabled,
        source_snapshot_id=snapshot.inventory_version,
        source_snapshot_sha256=snapshot.content_sha256,
        outcome_period_count=len(set(periods)),
        basis_source_id_count=len(basis_ids),
    )


def check_readiness_gates(
    evidence: MergeSplitReadinessInput,
    policy: DecisionPolicy,
) -> MergeSplitReadinessResult:
    """Evaluate the measured evidence against the governed maturity thresholds."""
    min_days = _require_int(policy, "min_observation_days")
    min_labels = _require_int(policy, "min_mature_labels")
    min_stores = _require_int(policy, "min_active_stores")
    min_pairs = _require_int(policy, "min_adjacent_pairs")
    min_clusters = _require_int(policy, "min_metro_clusters")
    min_contiguity = _require_float(policy, "min_spatial_contiguity")
    max_cv = _require_float(policy, "max_absorption_cv")
    max_psi = _require_float(policy, "max_drift_psi")
    max_wasserstein = _require_float(policy, "max_wasserstein")

    reasons: list[str] = []

    if evidence.governed_disabled:
        reasons.append("governed_disabled_by_data_contract_maturity")

    if evidence.is_synthetic:
        reasons.append("synthetic_inventory_refused")

    if not evidence.source_snapshot_id.strip():
        reasons.append("missing_source_snapshot_id")
    if not evidence.source_snapshot_sha256.strip():
        reasons.append("missing_source_snapshot_integrity_hash")

    if evidence.basis_source_id_count <= 0:
        reasons.append("no_hz004_basis_snapshots_in_outcome_history")

    if evidence.observation_days < min_days:
        reasons.append(
            f"observation_horizon_insufficient: {evidence.observation_days}d < {min_days}d"
        )

    if evidence.mature_labels_count < min_labels:
        reasons.append(
            f"sample_size_insufficient: {evidence.mature_labels_count} labels < {min_labels}"
        )

    if evidence.active_store_count < min_stores:
        reasons.append(
            f"active_stores_insufficient: {evidence.active_store_count} stores < {min_stores}"
        )

    if evidence.adjacent_pairs_count < min_pairs:
        reasons.append(
            f"adjacent_pairs_insufficient: {evidence.adjacent_pairs_count} pairs < {min_pairs}"
        )

    if evidence.metro_clusters_count < min_clusters:
        reasons.append(
            f"regional_coverage_insufficient: {evidence.metro_clusters_count} clusters < {min_clusters}"
        )

    if evidence.spatial_contiguity_ratio < min_contiguity:
        reasons.append(
            f"spatial_contiguity_insufficient: {evidence.spatial_contiguity_ratio:.2f} < {min_contiguity:.2f}"
        )

    if evidence.absorption_ratio_cv is None:
        reasons.append("absorption_cv_unmeasured")
    elif evidence.absorption_ratio_cv > max_cv:
        reasons.append(
            f"absorption_cv_exceeds_threshold: {evidence.absorption_ratio_cv:.4f} > {max_cv:.4f}"
        )

    if evidence.drift_psi is None:
        reasons.append("drift_psi_unmeasured")
    elif evidence.drift_psi > max_psi:
        reasons.append(f"drift_psi_exceeds_threshold: {evidence.drift_psi:.4f} > {max_psi:.4f}")

    if evidence.wasserstein_distance is None:
        reasons.append("wasserstein_distance_unmeasured")
    elif evidence.wasserstein_distance > max_wasserstein:
        reasons.append(
            f"wasserstein_distance_exceeds_threshold: "
            f"{evidence.wasserstein_distance:.4f} > {max_wasserstein:.4f}"
        )

    metrics = {
        "observation_days": evidence.observation_days,
        "mature_labels_count": evidence.mature_labels_count,
        "active_store_count": evidence.active_store_count,
        "adjacent_pairs_count": evidence.adjacent_pairs_count,
        "metro_clusters_count": evidence.metro_clusters_count,
        "spatial_contiguity_ratio": evidence.spatial_contiguity_ratio,
        "absorption_ratio_cv": evidence.absorption_ratio_cv,
        "drift_psi": evidence.drift_psi,
        "wasserstein_distance": evidence.wasserstein_distance,
        "outcome_period_count": evidence.outcome_period_count,
        "basis_source_id_count": evidence.basis_source_id_count,
        "source_snapshot_id": evidence.source_snapshot_id,
        "source_snapshot_sha256": evidence.source_snapshot_sha256,
        "governed_disabled": evidence.governed_disabled,
    }

    return MergeSplitReadinessResult(
        eligible=len(reasons) == 0,
        reasons=tuple(reasons),
        metrics=metrics,
    )


def evaluate_merge_split(
    evidence: MergeSplitEvidence,
    *,
    policy: DecisionPolicy,
    evaluated_at: datetime | None = None,
    model_version: str = COMPOSITION_MODEL_VERSION,
) -> MergeSplitEvaluationResult:
    """Evaluate merge and split candidates from trusted HZ-004 outcome history."""
    eval_time = evaluated_at or datetime.now(UTC)
    readiness_input = derive_readiness_input(evidence, policy)
    readiness = check_readiness_gates(readiness_input, policy)

    if not readiness.eligible:
        return MergeSplitEvaluationResult(
            tenant_id=policy.tenant_id,
            abstained=True,
            abstain_reasons=readiness.reasons,
            readiness=readiness,
            proposals=(),
            evaluated_at=eval_time,
            model_version=model_version,
            policy_version_id=policy.policy_version_id,
            evidence_summary=evidence.to_dict(),
        )

    min_rho = _require_float(policy, "min_correlation_rho")
    max_disconnect = _require_float(policy, "max_disconnect_index")
    min_split_ratio = _require_float(policy, "min_split_density_ratio")
    min_ndcg_gain = _require_float(policy, "min_ndcg_gain")
    min_var_reduction = _require_float(policy, "min_cannibalization_variance_reduction")
    min_paired_periods = _require_int(policy, "min_paired_periods")
    min_side_periods = _require_int(policy, "min_split_side_periods")
    allow_cross_admin = _require_bool(policy, "allow_cross_admin_boundary")

    cell_map = evidence.cell_map()
    proposals: list[MergeSplitProposal] = []
    declined: list[dict[str, Any]] = []

    for left_id, right_id in evidence.adjacency:
        left = cell_map.get(left_id)
        right = cell_map.get(right_id)
        if left is None or right is None:
            continue
        if not allow_cross_admin and (
            left.admin_city != right.admin_city
            or left.admin_district != right.admin_district
        ):
            declined.append(
                {
                    "candidate": f"{left_id}+{right_id}",
                    "kind": CompositionKind.MERGED.value,
                    "reason": "cross_admin_boundary_not_permitted_by_policy",
                }
            )
            continue

        stats = compute_pair_statistics(
            left, right, cell_map, min_paired_periods=min_paired_periods
        )
        refusal = _merge_refusal(
            stats,
            min_paired_periods=min_paired_periods,
            min_rho=min_rho,
            max_disconnect=max_disconnect,
            min_ndcg_gain=min_ndcg_gain,
            min_var_reduction=min_var_reduction,
        )
        if refusal is not None:
            declined.append(
                {
                    "candidate": f"{left_id}+{right_id}",
                    "kind": CompositionKind.MERGED.value,
                    "reason": refusal,
                    "paired_periods": stats.paired_periods,
                }
            )
            continue

        assert stats.correlation_rho is not None
        assert stats.disconnect_index is not None
        assert stats.ndcg_gain is not None
        assert stats.variance_reduction is not None

        member_ids = (left_id, right_id)
        proposals.append(
            MergeSplitProposal(
                proposal_id=str(uuid4()),
                zone_id=generate_merged_zone_id(member_ids),
                tenant_id=policy.tenant_id,
                composition_kind=CompositionKind.MERGED,
                member_cell_ids=member_ids,
                parent_zone_id=None,
                ndcg_gain=round(stats.ndcg_gain, 4),
                cannibalization_variance_reduction=round(stats.variance_reduction, 4),
                correlation_rho=round(stats.correlation_rho, 4),
                disconnect_index=round(stats.disconnect_index, 4),
                confidence=round(
                    _merge_confidence(stats.correlation_rho, stats.disconnect_index), 4
                ),
                model_version=model_version,
                policy_version_id=policy.policy_version_id,
                reasons=(
                    f"absorption_correlation_over_{stats.paired_periods}_shared_periods",
                    "demand_continuous_across_boundary",
                    "counterfactual_ndcg_outperformance",
                    "cannibalization_variance_reduced_under_joint_model",
                    f"source_snapshot:{readiness_input.source_snapshot_id}",
                ),
            )
        )

    proposals.extend(
        _evaluate_splits(
            evidence,
            policy=policy,
            model_version=model_version,
            min_split_ratio=min_split_ratio,
            min_side_periods=min_side_periods,
            source_snapshot_id=readiness_input.source_snapshot_id,
            declined=declined,
        )
    )

    return MergeSplitEvaluationResult(
        tenant_id=policy.tenant_id,
        abstained=False,
        abstain_reasons=(),
        readiness=readiness,
        proposals=tuple(proposals),
        evaluated_at=eval_time,
        model_version=model_version,
        policy_version_id=policy.policy_version_id,
        evidence_summary=evidence.to_dict(),
        declined=tuple(declined),
    )


def _merge_refusal(
    stats: PairStatistics,
    *,
    min_paired_periods: int,
    min_rho: float,
    max_disconnect: float,
    min_ndcg_gain: float,
    min_var_reduction: float,
) -> str | None:
    """Return why this pair cannot be merged, or None when it qualifies."""
    if stats.paired_periods < min_paired_periods:
        return (
            f"insufficient_shared_outcome_periods: "
            f"{stats.paired_periods} < {min_paired_periods}"
        )
    if stats.correlation_rho is None:
        return "correlation_unmeasurable_on_observed_absorption"
    if stats.correlation_rho < min_rho:
        return f"correlation_below_threshold: {stats.correlation_rho:.4f} < {min_rho:.4f}"
    if stats.disconnect_index is None:
        return "disconnect_index_unmeasurable"
    if stats.disconnect_index > max_disconnect:
        return (
            f"demand_disconnect_above_threshold: "
            f"{stats.disconnect_index:.4f} > {max_disconnect:.4f}"
        )
    if stats.ndcg_gain is None:
        return "ndcg_gain_unmeasurable_on_observed_periods"
    if stats.ndcg_gain < min_ndcg_gain:
        return f"ndcg_gain_below_threshold: {stats.ndcg_gain:.4f} < {min_ndcg_gain:.4f}"
    if stats.variance_reduction is None:
        return "cannibalization_variance_reduction_unmeasurable"
    if stats.variance_reduction < min_var_reduction:
        return (
            f"cannibalization_variance_reduction_below_threshold: "
            f"{stats.variance_reduction:.4f} < {min_var_reduction:.4f}"
        )
    return None


def _merge_confidence(rho: float, disconnect: float) -> float:
    return min(1.0, max(0.0, (rho + (1.0 - disconnect)) / 2.0))


def compute_pair_statistics(
    left: CellOutcomeSeries,
    right: CellOutcomeSeries,
    cell_map: Mapping[str, CellOutcomeSeries],
    *,
    min_paired_periods: int,
) -> PairStatistics:
    """Measure the merge case for one adjacent pair over its shared periods."""
    shared = aligned_periods(left, right)
    if len(shared) < max(min_paired_periods, 2):
        return PairStatistics(len(shared), None, None, None, None)

    # Correlation and the boundary step read the two cells' *demand*: cells in
    # one trade area share a demand environment, whereas how the zone's take
    # splits between them is the thing a merge exists to model.
    left_demand = [left.demand_by_period()[period] for period in shared]
    right_demand = [right.demand_by_period()[period] for period in shared]

    return PairStatistics(
        paired_periods=len(shared),
        correlation_rho=pearson_correlation(left_demand, right_demand),
        disconnect_index=_disconnect_index(left_demand, right_demand),
        ndcg_gain=_ndcg_gain(left, right, cell_map, shared),
        variance_reduction=_cannibalization_variance_reduction(left, right, shared),
    )


def pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation of two equal-length series, or None if undefined.

    For a merge candidate this runs over the two cells' demand series, which is
    what the readiness rule means by activity-and-revenue correlation: cells in
    one trade area share a demand environment. It deliberately does not run over
    *absorbed* demand -- how the zone's take splits between its cells is the
    thing a merge exists to model, so correlating on it would penalise exactly
    the pairs that most need merging.

    A constant series has no correlation with anything -- returning None keeps
    that case out of the proposal path instead of letting a degenerate 1.0
    through, which is how a pair with no real co-movement previously qualified.
    """
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    var_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    return max(-1.0, min(1.0, cov / (var_x * var_y)))


def _disconnect_index(
    left_demand: Sequence[float], right_demand: Sequence[float]
) -> float | None:
    """Mean relative demand step across the shared boundary, in [0, 1].

    The readiness rule pairs its correlation threshold with a *demand
    discontinuity* limit: co-movement alone would let a busy cell absorb a quiet
    neighbour, so the two also have to sit at a comparable level. Each period
    contributes the gap as a share of the larger side, which keeps the index
    scale-free and bounded.
    """
    if not left_demand or len(left_demand) != len(right_demand):
        return None
    gaps: list[float] = []
    for left, right in zip(left_demand, right_demand, strict=True):
        larger = max(abs(left), abs(right))
        if larger <= 0.0:
            # Neither side carried demand in this period; there is no boundary
            # step to measure, and calling it continuous would be a guess.
            return None
        gaps.append(abs(left - right) / larger)
    return sum(gaps) / len(gaps)


def _ndcg_gain(
    left: CellOutcomeSeries,
    right: CellOutcomeSeries,
    cell_map: Mapping[str, CellOutcomeSeries],
    shared: Sequence[tuple[date, date]],
) -> float | None:
    """Ranking quality the merge buys, replayed over the observed periods.

    Both arms rank the same atomic cells against the same realised relevance, so
    the number of ranked units never changes. What changes is the predictor: in
    the atomic arm each cell is predicted by its own previous-period absorption;
    in the merged arm the two candidate cells share one trade-area level -- the
    pair's previous-period mean -- because a merged zone can no longer tell its
    members apart.

    Pooling helps exactly when the within-pair difference is transient noise and
    hurts when it is persistent structure, which is the question "are these one
    trade area?" asked in ranking terms. A pair whose members sit at genuinely
    different levels loses NDCG under pooling and is refused. Keeping the unit
    count fixed matters: collapsing two units into one would move NDCG on
    cardinality alone, and every pair would look improved.
    """
    if len(shared) < 2:
        return None

    ranked_cells = [
        cell
        for cell in cell_map.values()
        if all(period in cell.absorbed_by_period() for period in shared)
    ]
    if len(ranked_cells) < 3:
        # Below three units the ranking is near-perfect either way and the
        # comparison carries no information.
        return None

    merged_ids = {left.cell_id, right.cell_id}
    gains: list[float] = []
    for index in range(1, len(shared)):
        prior, current = shared[index - 1], shared[index]
        pooled_prior = sum(
            cell_map[cid].absorbed_by_period()[prior] for cid in merged_ids
        ) / len(merged_ids)

        atomic_units: list[tuple[float, float]] = []
        merged_units: list[tuple[float, float]] = []
        for cell in ranked_cells:
            absorbed = cell.absorbed_by_period()
            relevance = absorbed[current]
            atomic_units.append((absorbed[prior], relevance))
            merged_units.append(
                (
                    pooled_prior if cell.cell_id in merged_ids else absorbed[prior],
                    relevance,
                )
            )

        atomic_ndcg = _ndcg(atomic_units)
        merged_ndcg = _ndcg(merged_units)
        if atomic_ndcg is None or merged_ndcg is None:
            continue
        gains.append(merged_ndcg - atomic_ndcg)

    if not gains:
        return None
    return sum(gains) / len(gains)


def _ndcg(units: Sequence[tuple[float, float]]) -> float | None:
    """NDCG of a (prediction, relevance) ranking; None when it is undefined."""
    if len(units) < 2:
        return None
    relevances = [relevance for _, relevance in units]
    if all(relevance <= 0 for relevance in relevances):
        return None

    ordered = sorted(units, key=lambda unit: unit[0], reverse=True)
    dcg = sum(
        relevance / math.log2(rank + 2)
        for rank, (_, relevance) in enumerate(ordered)
    )
    ideal = sum(
        relevance / math.log2(rank + 2)
        for rank, relevance in enumerate(sorted(relevances, reverse=True))
    )
    if ideal <= 0:
        return None
    return dcg / ideal


def _cannibalization_variance_reduction(
    left: CellOutcomeSeries,
    right: CellOutcomeSeries,
    shared: Sequence[tuple[date, date]],
) -> float | None:
    """Residual variance a zone-level dilution model removes, versus per-cell.

    Cannibalization is the part of a cell's per-store take that its *neighbour's*
    stores explain. Both arms fit the same shape -- per-store absorption
    regressed on a store count -- and differ only in which count: the cell's own
    in the independent arm, the pair's total in the joint arm. Each fit is
    leave-one-out, so no period contributes to the line it is scored against.

    Where the two cells share customers, adding stores next door dilutes this
    cell, and the zone-level count tracks the take better. Where they are
    separate trade areas the neighbour's count is noise and the reduction comes
    out at or below zero. The measure is therefore free to fail, and unlike a
    residual-correlation statistic it is not a restatement of `correlation_rho`.

    Returns None when the fit is not identified -- constant store counts, or a
    period with no absorbing store -- so an unmeasurable pair is refused rather
    than scored.
    """
    if len(shared) < 3:
        return None

    per_store: dict[str, list[float]] = {"left": [], "right": []}
    own_counts: dict[str, list[float]] = {"left": [], "right": []}
    pair_counts: list[float] = []

    left_by_period = {o.period: o for o in left.outcomes}
    right_by_period = {o.period: o for o in right.outcomes}
    for period in shared:
        left_outcome = left_by_period[period]
        right_outcome = right_by_period[period]
        if left_outcome.absorbing_store_count <= 0 or right_outcome.absorbing_store_count <= 0:
            return None
        per_store["left"].append(
            left_outcome.absorbed_demand / left_outcome.absorbing_store_count
        )
        per_store["right"].append(
            right_outcome.absorbed_demand / right_outcome.absorbing_store_count
        )
        own_counts["left"].append(float(left_outcome.absorbing_store_count))
        own_counts["right"].append(float(right_outcome.absorbing_store_count))
        pair_counts.append(
            float(left_outcome.absorbing_store_count + right_outcome.absorbing_store_count)
        )

    independent_residuals: list[float] = []
    joint_residuals: list[float] = []
    for side in ("left", "right"):
        independent = _loo_regression_residuals(own_counts[side], per_store[side])
        joint = _loo_regression_residuals(pair_counts, per_store[side])
        if independent is None or joint is None:
            return None
        independent_residuals.extend(independent)
        joint_residuals.extend(joint)

    independent_mse = _mean_square(independent_residuals)
    if independent_mse <= 0.0:
        return None
    return 1.0 - (_mean_square(joint_residuals) / independent_mse)


def _loo_regression_residuals(
    regressor: Sequence[float], response: Sequence[float]
) -> list[float] | None:
    """Leave-one-out simple-regression residuals, or None if unidentified."""
    periods = len(regressor)
    if periods != len(response) or periods < 3:
        return None

    residuals: list[float] = []
    for index in range(periods):
        others = [i for i in range(periods) if i != index]
        xs = [regressor[i] for i in others]
        ys = [response[i] for i in others]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        var_x = sum((x - mean_x) ** 2 for x in xs)
        if var_x <= 0.0:
            # A constant regressor identifies no slope; the dilution question
            # cannot be answered from this history.
            return None
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / var_x
        intercept = mean_y - slope * mean_x
        residuals.append(response[index] - (intercept + slope * regressor[index]))
    return residuals


def _mean_square(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(value * value for value in values) / len(values)


def _evaluate_splits(
    evidence: MergeSplitEvidence,
    *,
    policy: DecisionPolicy,
    model_version: str,
    min_split_ratio: float,
    min_side_periods: int,
    source_snapshot_id: str,
    declined: list[dict[str, Any]],
) -> list[MergeSplitProposal]:
    """Split existing zones whose sides show divergent realised demand density.

    A split candidate is an *existing* active zone, and its children are the
    zone's own member cells grouped by the barrier side their HZ-004 outcomes
    were recorded on. No side evidence means no split: the engine has no way to
    tell where the boundary would fall, and inventing one would be exactly the
    geometry-only guess the readiness ruling forbids.
    """
    cell_map = evidence.cell_map()
    proposals: list[MergeSplitProposal] = []

    for zone in evidence.existing_zones:
        if zone.composition_kind != CompositionKind.MERGED.value:
            continue
        if len(zone.member_cell_ids) < 2:
            continue

        sides: dict[str, list[str]] = {}
        side_series: dict[str, dict[tuple[date, date], float]] = {}
        missing_evidence = False
        for cell_id in zone.member_cell_ids:
            cell = cell_map.get(cell_id)
            if cell is None or not cell.side_outcomes:
                missing_evidence = True
                break
            labels = {o.barrier_side for o in cell.side_outcomes if o.barrier_side}
            if len(labels) != 1:
                # A cell straddling the barrier cannot be assigned to a child.
                missing_evidence = True
                break
            side = labels.pop()
            sides.setdefault(side, []).append(cell_id)
            for outcome in cell.side_outcomes:
                bucket = side_series.setdefault(side, {})
                bucket[outcome.period] = bucket.get(outcome.period, 0.0) + outcome.absorbed_demand

        if missing_evidence or len(sides) != 2:
            declined.append(
                {
                    "candidate": zone.zone_id,
                    "kind": CompositionKind.SPLIT_CHILD.value,
                    "reason": "no_side_labelled_hz004_outcomes_for_every_member_cell",
                }
            )
            continue

        side_a, side_b = sorted(sides)
        shared = sorted(set(side_series[side_a]) & set(side_series[side_b]))
        if len(shared) < min_side_periods:
            declined.append(
                {
                    "candidate": zone.zone_id,
                    "kind": CompositionKind.SPLIT_CHILD.value,
                    "reason": (
                        f"insufficient_side_outcome_periods: "
                        f"{len(shared)} < {min_side_periods}"
                    ),
                }
            )
            continue

        mean_a = sum(side_series[side_a][period] for period in shared) / len(shared)
        mean_b = sum(side_series[side_b][period] for period in shared) / len(shared)
        weaker = min(mean_a, mean_b)
        if weaker <= 0.0:
            declined.append(
                {
                    "candidate": zone.zone_id,
                    "kind": CompositionKind.SPLIT_CHILD.value,
                    "reason": "one_side_absorbed_nothing_measurable",
                }
            )
            continue

        density_ratio = max(mean_a, mean_b) / weaker
        if density_ratio < min_split_ratio:
            declined.append(
                {
                    "candidate": zone.zone_id,
                    "kind": CompositionKind.SPLIT_CHILD.value,
                    "reason": (
                        f"side_density_ratio_below_threshold: "
                        f"{density_ratio:.4f} < {min_split_ratio:.4f}"
                    ),
                }
            )
            continue

        ratio_series_a = [side_series[side_a][period] for period in shared]
        ratio_series_b = [side_series[side_b][period] for period in shared]
        rho = pearson_correlation(ratio_series_a, ratio_series_b)
        disconnect = _disconnect_index(
            _normalized(ratio_series_a), _normalized(ratio_series_b)
        )
        barrier = next(
            (
                cell_map[cid].barrier_description
                for cid in zone.member_cell_ids
                if cell_map.get(cid) and cell_map[cid].barrier_description
            ),
            "recorded_natural_barrier",
        )

        for index, side in enumerate((side_a, side_b), start=1):
            member_ids = tuple(sorted(sides[side]))
            proposals.append(
                MergeSplitProposal(
                    proposal_id=str(uuid4()),
                    zone_id=generate_merged_zone_id(member_ids),
                    tenant_id=policy.tenant_id,
                    composition_kind=CompositionKind.SPLIT_CHILD,
                    member_cell_ids=member_ids,
                    parent_zone_id=zone.zone_id,
                    ndcg_gain=0.0,
                    cannibalization_variance_reduction=0.0,
                    correlation_rho=round(rho, 4) if rho is not None else 0.0,
                    disconnect_index=round(disconnect, 4) if disconnect is not None else 0.0,
                    split_density_ratio=round(density_ratio, 2),
                    confidence=round(min(1.0, density_ratio / (2.0 * min_split_ratio)), 4),
                    model_version=model_version,
                    policy_version_id=policy.policy_version_id,
                    reasons=(
                        f"side_labelled_absorption_density_ratio_{density_ratio:.2f}",
                        f"measured_over_{len(shared)}_shared_periods",
                        f"barrier:{barrier}",
                        f"child_partition_{index}_of_2_side_{side}",
                        f"source_snapshot:{source_snapshot_id}",
                    ),
                )
            )

    return proposals


def _normalized(values: Sequence[float]) -> list[float]:
    peak = max(values) if values else 0.0
    if peak <= 0.0:
        return [0.0 for _ in values]
    return [value / peak for value in values]


__all__ = [
    "DECISION_POLICY_KEYS",
    "READINESS_POLICY_KEYS",
    "MergeSplitEvaluationResult",
    "MergeSplitPolicyError",
    "MergeSplitProposal",
    "MergeSplitReadinessInput",
    "MergeSplitReadinessResult",
    "PairStatistics",
    "check_readiness_gates",
    "compute_pair_statistics",
    "derive_readiness_input",
    "evaluate_merge_split",
    "pearson_correlation",
]
