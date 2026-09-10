"""Trusted HZ-004 outcome evidence for heat-zone merge/split (ODP-FR-HZ-006).

`ODP-FR-HZ-006` may only act on realised absorption history produced by the
`ODP-FR-HZ-004` closed loop. The readiness ruling
(`docs/evidence/ODP_HZ006_MERGE_SPLIT_READINESS_2026-09-03.md`) is explicit that
the decision must never rest on spatial adjacency or on numbers a caller hands
in: production currently carries **0** mature heat-zone labels, so any request
that can name its own maturity can talk the engine past a gate the production
snapshot fails.

This module is the only door through which merge/split evidence enters. Every
quantity the readiness gate reads is derived here from two sources the caller
cannot address:

1. The release-bound PG16 model-ready inventory receipt
   (`models/shared_ml/model_ready_inventory_receipt.json`), loaded and
   integrity-checked by `load_model_ready_receipt`. It supplies mature-label
   counts and the snapshot identity the evaluation is bound to.
2. Persisted HZ-004 absorption outcomes and H3 adjacency, read from the
   server-side evidence repository. Each outcome is an `AbsorptionResult` the
   absorption pipeline computed from `StoreDailyPerformance` -- it carries its
   own basis snapshot ids and cannot be minted by an API client.

Horizon, sample size, geographic coverage and stability are *measured* from that
history rather than declared. When the history is too short or too sparse to
measure a dimension, the dimension is reported as unmeasured, which the
readiness gate treats as a failure -- absence of evidence never reads as
evidence of readiness.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from models.model_ready.contracts import MODEL_SPECS
from models.shared_ml.model_ready_receipt import (
    ModelReadyReceiptError,
    load_model_ready_receipt,
)

__all__ = [
    "HEATZONE_CAPABILITY_KEY",
    "AbsorptionOutcomeRecord",
    "CellOutcomeSeries",
    "EvidenceUnavailableError",
    "ExistingZoneComposition",
    "InventorySnapshotFacts",
    "MergeSplitEvidence",
    "MergeSplitEvidenceRepository",
    "assemble_merge_split_evidence",
    "load_inventory_snapshot_facts",
    "population_stability_index",
    "wasserstein_distance_1d",
]

#: Capability key of the heat-zone training view inside the inventory receipt.
HEATZONE_CAPABILITY_KEY = "heatzone"

#: Absorption ratios live in [0, 1]; drift statistics bin that fixed support so
#: two halves of the history are compared on the same scale.
_DRIFT_BIN_COUNT = 10
_DRIFT_EPSILON = 1e-6


class EvidenceUnavailableError(RuntimeError):
    """Raised when trusted HZ-004 evidence cannot be assembled at all.

    Distinct from "the evidence is immature": an unreadable receipt or an
    unwired evidence repository means the service cannot even tell whether it is
    allowed to act, and must fail closed rather than abstain with metrics.
    """


@dataclass(frozen=True)
class AbsorptionOutcomeRecord:
    """One persisted HZ-004 absorption outcome for one cell over one period.

    Mirrors `expansion.heatzone_absorption_outcomes`, which the absorption
    pipeline writes from `compute_absorbed_demand`. `barrier_side` is set only
    when the geo pipeline has partitioned the cell's stores across a recorded
    natural barrier; it is the sole admissible basis for a split.
    """

    cell_id: str
    period_start: date
    period_end: date
    original_demand: float
    absorbed_demand: float
    remaining_demand: float
    absorption_ratio: float
    absorbing_store_count: int
    basis_source_ids: tuple[str, ...]
    absorption_policy_version_id: str
    basis_at: datetime
    under_realized: bool = False
    barrier_side: str | None = None
    barrier_description: str = ""

    @property
    def period(self) -> tuple[date, date]:
        return (self.period_start, self.period_end)


@dataclass(frozen=True)
class CellOutcomeSeries:
    """A cell's identity plus its whole-cell HZ-004 outcome history."""

    cell_id: str
    h3_index: str
    admin_city: str = ""
    admin_district: str = ""
    outcomes: tuple[AbsorptionOutcomeRecord, ...] = ()
    side_outcomes: tuple[AbsorptionOutcomeRecord, ...] = ()

    @property
    def barrier_description(self) -> str:
        for outcome in self.side_outcomes:
            if outcome.barrier_description:
                return outcome.barrier_description
        return ""

    def absorbed_by_period(self) -> dict[tuple[date, date], float]:
        return {o.period: o.absorbed_demand for o in self.outcomes}

    def demand_by_period(self) -> dict[tuple[date, date], float]:
        return {o.period: o.original_demand for o in self.outcomes}


@dataclass(frozen=True)
class ExistingZoneComposition:
    """An active composition already persisted for this tenant.

    Split candidates are drawn from here rather than invented: a `SPLIT_CHILD`
    partitions the members of a zone that actually exists, so `parent_zone_id`
    always names a row the composition table can join to.
    """

    zone_id: str
    composition_kind: str
    member_cell_ids: tuple[str, ...]


@dataclass(frozen=True)
class InventorySnapshotFacts:
    """Maturity facts read from the integrity-checked PG16 inventory receipt."""

    inventory_version: str
    content_sha256: str
    observed_at: str
    relation: str
    view_version: str
    observed_count: int
    eligible_count: int
    minimum_rows: int
    auto_seeded: bool

    @property
    def governed_disabled(self) -> bool:
        """True while the production data contract has not matured.

        Mirrors the `DATA_CONTRACT_NOT_MATURE` binding status recorded in the
        readiness evidence: below the contract's minimum row count the model is
        governed-disabled in production, and merge/split must not run.
        """
        return self.observed_count <= 0 or self.eligible_count < self.minimum_rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_version": self.inventory_version,
            "content_sha256": self.content_sha256,
            "observed_at": self.observed_at,
            "relation": self.relation,
            "view_version": self.view_version,
            "observed_count": self.observed_count,
            "eligible_count": self.eligible_count,
            "minimum_rows": self.minimum_rows,
            "auto_seeded": self.auto_seeded,
            "governed_disabled": self.governed_disabled,
        }


class MergeSplitEvidenceRepository(Protocol):
    """Server-side reader for persisted HZ-004 evidence.

    Implementations read the absorption outcomes and adjacency the pipelines
    wrote. No method accepts caller-supplied measurements.
    """

    def list_absorption_outcomes(self, tenant_id: str) -> list[AbsorptionOutcomeRecord]:
        ...

    def list_cells(self, tenant_id: str) -> list[CellOutcomeSeries]:
        ...

    def list_adjacency(self, tenant_id: str) -> list[tuple[str, str]]:
        ...


@dataclass(frozen=True)
class MergeSplitEvidence:
    """Everything the engine is allowed to reason from, all server-derived."""

    tenant_id: str
    snapshot: InventorySnapshotFacts
    cells: tuple[CellOutcomeSeries, ...]
    adjacency: tuple[tuple[str, str], ...]
    existing_zones: tuple[ExistingZoneComposition, ...] = ()
    assembled_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def cell_map(self) -> dict[str, CellOutcomeSeries]:
        return {c.cell_id: c for c in self.cells}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "snapshot": self.snapshot.to_dict(),
            "cell_count": len(self.cells),
            "adjacency_edge_count": len(self.adjacency),
            "existing_zone_count": len(self.existing_zones),
            "assembled_at": self.assembled_at.isoformat(),
        }


def load_inventory_snapshot_facts(
    *,
    capability: str = HEATZONE_CAPABILITY_KEY,
    receipt_path: Path | None = None,
) -> InventorySnapshotFacts:
    """Read maturity facts for `capability` from the release-bound receipt.

    The receipt loader verifies the content hash and refuses an auto-seeded
    receipt, so a tampered or synthetic inventory raises instead of silently
    unlocking the gate. `receipt_path` exists so that verification can itself be
    tested against a fixture; it is not reachable from the request path, where
    the release-bound receipt is always the one that is read.
    """
    try:
        payload = (
            load_model_ready_receipt(receipt_path)
            if receipt_path is not None
            else load_model_ready_receipt()
        )
    except ModelReadyReceiptError as exc:
        raise EvidenceUnavailableError(
            f"PG16 model-ready inventory receipt is not trustworthy: {exc}"
        ) from exc

    capabilities = payload.get("capabilities", {})
    entry = capabilities.get(capability)
    if not isinstance(entry, Mapping):
        raise EvidenceUnavailableError(
            f"inventory receipt declares no '{capability}' capability; "
            "merge/split maturity cannot be established"
        )

    spec = MODEL_SPECS.get(capability)
    if spec is None:
        raise EvidenceUnavailableError(
            f"no production model spec is registered for '{capability}'"
        )

    integrity = payload.get("integrity", {})
    return InventorySnapshotFacts(
        inventory_version=str(payload.get("inventory_version", "")),
        content_sha256=str(
            integrity.get("content_sha256", "") if isinstance(integrity, Mapping) else ""
        ),
        observed_at=str(payload.get("observed_at", "")),
        relation=str(entry.get("relation", "")),
        view_version=str(entry.get("view_version", "")),
        observed_count=int(entry.get("observed_count", 0)),
        eligible_count=int(entry.get("eligible_count", 0)),
        minimum_rows=int(spec.minimum_rows),
        auto_seeded=bool(payload.get("auto_seeded", False)),
    )


def assemble_merge_split_evidence(
    repository: MergeSplitEvidenceRepository | None,
    *,
    tenant_id: str,
    existing_zones: Sequence[ExistingZoneComposition] = (),
    capability: str = HEATZONE_CAPABILITY_KEY,
    receipt_path: Path | None = None,
) -> MergeSplitEvidence:
    """Assemble the trusted evidence bundle for one tenant.

    Raises `EvidenceUnavailableError` when no evidence repository is wired: an
    unwired reader is indistinguishable from "no history exists", and the safe
    reading of that ambiguity is to refuse rather than to evaluate on nothing.
    """
    if repository is None:
        raise EvidenceUnavailableError(
            "no HZ-004 absorption evidence repository is wired; heat-zone "
            "merge/split cannot be evaluated without trusted outcome history"
        )

    snapshot = load_inventory_snapshot_facts(
        capability=capability, receipt_path=receipt_path
    )

    cells = tuple(repository.list_cells(tenant_id))
    adjacency = tuple(
        _normalize_edge(a, b)
        for a, b in repository.list_adjacency(tenant_id)
        if a and b and a != b
    )
    return MergeSplitEvidence(
        tenant_id=tenant_id,
        snapshot=snapshot,
        cells=cells,
        adjacency=tuple(sorted(set(adjacency))),
        existing_zones=tuple(existing_zones),
    )


def _normalize_edge(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# Measured readiness dimensions
# ---------------------------------------------------------------------------


def contiguous_observation_days(cells: Iterable[CellOutcomeSeries]) -> int:
    """Longest gap-free span, in days, covered by the pooled outcome calendar.

    Readiness threshold 3.1 asks for continuous realised history, so periods are
    merged into runs and a run only extends across a boundary when the next
    period starts the day after the previous one ends. A hole in the calendar
    truncates the run instead of being averaged away.
    """
    periods: set[tuple[date, date]] = set()
    for cell in cells:
        for outcome in cell.outcomes:
            if outcome.period_end < outcome.period_start:
                continue
            periods.add(outcome.period)
    if not periods:
        return 0

    ordered = sorted(periods)
    best = 0
    run_start, run_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= run_end + timedelta(days=1):
            run_end = max(run_end, end)
            continue
        best = max(best, (run_end - run_start).days + 1)
        run_start, run_end = start, end
    return max(best, (run_end - run_start).days + 1)


def aligned_periods(
    left: CellOutcomeSeries, right: CellOutcomeSeries
) -> list[tuple[date, date]]:
    """Periods for which both cells carry a whole-cell outcome, in time order."""
    shared = set(left.absorbed_by_period()) & set(right.absorbed_by_period())
    return sorted(shared)


def count_eligible_pairs(
    adjacency: Sequence[tuple[str, str]],
    cell_map: Mapping[str, CellOutcomeSeries],
    *,
    min_paired_periods: int,
) -> int:
    """Adjacent pairs with enough jointly-observed periods to be testable."""
    count = 0
    for a, b in adjacency:
        left = cell_map.get(a)
        right = cell_map.get(b)
        if left is None or right is None:
            continue
        if len(aligned_periods(left, right)) >= min_paired_periods:
            count += 1
    return count


def spatial_contiguity_ratio(
    adjacency: Sequence[tuple[str, str]], cell_map: Mapping[str, CellOutcomeSeries]
) -> float:
    """Share of adjacency-graph cells that carry any realised outcome."""
    graph_cells = {cell for edge in adjacency for cell in edge}
    if not graph_cells:
        return 0.0
    observed = sum(
        1
        for cell_id in graph_cells
        if cell_map.get(cell_id) is not None and cell_map[cell_id].outcomes
    )
    return observed / len(graph_cells)


def coefficient_of_variation(values: Sequence[float]) -> float | None:
    """Sample CV, or None when fewer than two points make it undefined."""
    usable = [float(v) for v in values]
    if len(usable) < 2:
        return None
    mean = sum(usable) / len(usable)
    if mean <= 0:
        return None
    variance = sum((v - mean) ** 2 for v in usable) / (len(usable) - 1)
    return math.sqrt(variance) / mean


def population_stability_index(
    expected: Sequence[float], actual: Sequence[float], *, bins: int = _DRIFT_BIN_COUNT
) -> float | None:
    """PSI between two absorption-ratio samples over the fixed [0, 1] support.

    Returns None when either sample is empty, so an unmeasurable drift reads as
    unmeasured rather than as zero drift.
    """
    if not expected or not actual:
        return None
    expected_hist = _histogram(expected, bins)
    actual_hist = _histogram(actual, bins)
    psi = 0.0
    for exp_share, act_share in zip(expected_hist, actual_hist, strict=True):
        exp_share = max(exp_share, _DRIFT_EPSILON)
        act_share = max(act_share, _DRIFT_EPSILON)
        psi += (act_share - exp_share) * math.log(act_share / exp_share)
    return psi


def wasserstein_distance_1d(
    expected: Sequence[float], actual: Sequence[float]
) -> float | None:
    """First Wasserstein distance between two 1-D samples on a quantile grid."""
    if not expected or not actual:
        return None
    left = sorted(float(v) for v in expected)
    right = sorted(float(v) for v in actual)
    steps = max(len(left), len(right), 2)
    total = 0.0
    for index in range(steps):
        quantile = (index + 0.5) / steps
        total += abs(_quantile(left, quantile) - _quantile(right, quantile))
    return total / steps


def _histogram(values: Sequence[float], bins: int) -> list[float]:
    counts = [0] * bins
    for value in values:
        clamped = min(max(float(value), 0.0), 1.0)
        index = min(int(clamped * bins), bins - 1)
        counts[index] += 1
    total = float(len(values))
    return [count / total for count in counts]


def _quantile(sorted_values: Sequence[float], quantile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = quantile * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight
