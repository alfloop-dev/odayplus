"""Builders for trusted HZ-004 merge/split evidence (ODP-FR-HZ-006).

Two things these helpers deliberately do *not* do.

They do not let a test hand the engine a readiness verdict. Everything the
readiness gate reads is measured from the outcome rows built here, exactly as it
would be measured from `expansion.heatzone_absorption_outcomes` in production,
so a fixture that clears the gate has to actually carry six months of
gap-free, low-drift absorption history across two metropolitan clusters.

They do not fake the inventory receipt's integrity envelope. `matured_receipt`
writes a real receipt file and computes its content hash with the same function
the loader verifies against, so the loader's checks run for real; only the
recorded label counts differ from the release-bound receipt, which is the one
fact a test cannot obtain by waiting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from models.shared_ml.model_ready_receipt import compute_receipt_sha256
from modules.heatzone.application.merge_split_evidence import AbsorptionOutcomeRecord
from modules.heatzone.infrastructure import (
    CellRegistration,
    InMemoryMergeSplitEvidenceRepository,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
ABSORPTION_POLICY_ID = f"heatzone-absorption-v1:{TENANT_ID}"

#: Eight back-to-back 28-day periods -- 224 days, clearing the 180-day horizon.
PERIOD_COUNT = 8
PERIOD_DAYS = 28
FIRST_PERIOD_START = date(2026, 1, 5)

#: The pair the merge case is built around. Store counts repeat every four
#: periods so the two halves of the history carry the same absorption-ratio
#: distribution and the drift statistics stay near zero, while the pair's total
#: still moves enough for the zone-level dilution fit to be identified.
MERGE_LEFT = "cell-taipei-00"
MERGE_RIGHT = "cell-taipei-01"
_LEFT_STORES = (2, 6, 4, 8, 6, 2, 8, 4)
_RIGHT_STORES = (6, 2, 8, 4, 2, 6, 4, 8)
_DEMAND_CYCLE = (1.00, 1.35, 1.05, 1.40, 1.00, 1.35, 1.05, 1.40)
#: A small idiosyncratic wobble on the right cell, so the pair's demand
#: correlation is earned rather than an artefact of one series being a multiple
#: of the other.
_RIGHT_DEMAND_WOBBLE = (1.00, 1.03, 0.98, 1.02, 1.00, 1.03, 0.98, 1.02)
_LEFT_DEMAND_BASE = 1400.0
_RIGHT_DEMAND_BASE = 1470.0
_ZONE_TAKE_SHARE = 0.50

#: Neighbouring cells that are not one trade area still have demand of their
#: own; rotating the shape by cell index gives each a different rhythm, which is
#: what makes their correlation fail on evidence rather than on emptiness.
_STEADY_SHAPE = (1.00, 1.10, 0.95, 1.05)
_STEADY_ABSORPTION_RATIO = 0.62

#: The zone the split case is built around, and the barrier that divides it.
SPLIT_LEFT = "cell-kaohsiung-00"
SPLIT_RIGHT = "cell-kaohsiung-01"
BARRIER_DESCRIPTION = "Provincial Highway 17 embankment"


def periods() -> list[tuple[date, date]]:
    return [
        (
            FIRST_PERIOD_START + timedelta(days=PERIOD_DAYS * index),
            FIRST_PERIOD_START + timedelta(days=PERIOD_DAYS * index + PERIOD_DAYS - 1),
        )
        for index in range(PERIOD_COUNT)
    ]


@dataclass(frozen=True)
class _CellPlan:
    cell_id: str
    city: str
    district: str
    demand: tuple[float, ...]
    absorbed: tuple[float, ...]
    stores: tuple[int, ...]


def _outcome(
    plan: _CellPlan,
    index: int,
    bounds: tuple[date, date],
    *,
    barrier_side: str | None = None,
    scale: float = 1.0,
) -> AbsorptionOutcomeRecord:
    demand = round(plan.demand[index] * scale, 2)
    absorbed = round(min(plan.absorbed[index] * scale, demand), 2)
    return AbsorptionOutcomeRecord(
        cell_id=plan.cell_id,
        period_start=bounds[0],
        period_end=bounds[1],
        original_demand=demand,
        absorbed_demand=absorbed,
        remaining_demand=round(demand - absorbed, 2),
        absorption_ratio=round(absorbed / demand, 4) if demand > 0 else 0.0,
        absorbing_store_count=plan.stores[index],
        basis_source_ids=(f"sdp-{plan.cell_id}-{bounds[0].isoformat()}",),
        absorption_policy_version_id=ABSORPTION_POLICY_ID,
        basis_at=datetime(2026, 9, 1, tzinfo=UTC),
        under_realized=False,
        barrier_side=barrier_side,
        barrier_description=BARRIER_DESCRIPTION if barrier_side else "",
    )


def _merge_pair_plans(
    left_stores: tuple[int, ...] = _LEFT_STORES,
    right_stores: tuple[int, ...] = _RIGHT_STORES,
) -> tuple[_CellPlan, _CellPlan]:
    """One trade area: a shared demand cycle split between the cells by stores.

    Store counts move in opposite directions while the pair total alternates
    between two levels, which is what lets the zone-level dilution fit be told
    apart from the per-cell one. Both patterns repeat every four periods, so the
    two halves of the history carry the same absorption-ratio distribution and
    the drift statistics have nothing to report.
    """
    left_demand: list[float] = []
    right_demand: list[float] = []
    left_absorbed: list[float] = []
    right_absorbed: list[float] = []
    for index in range(PERIOD_COUNT):
        cycle = _DEMAND_CYCLE[index]
        d_left = _LEFT_DEMAND_BASE * cycle
        d_right = _RIGHT_DEMAND_BASE * cycle * _RIGHT_DEMAND_WOBBLE[index]
        zone_take = _ZONE_TAKE_SHARE * (d_left + d_right)
        total_stores = left_stores[index] + right_stores[index]
        left_demand.append(d_left)
        right_demand.append(d_right)
        left_absorbed.append(zone_take * left_stores[index] / total_stores)
        right_absorbed.append(zone_take * right_stores[index] / total_stores)
    return (
        _CellPlan(MERGE_LEFT, "Taipei", "Xinyi", tuple(left_demand),
                  tuple(left_absorbed), left_stores),
        _CellPlan(MERGE_RIGHT, "Taipei", "Xinyi", tuple(right_demand),
                  tuple(right_absorbed), right_stores),
    )


def _steady_plan(
    cell_id: str, city: str, district: str, level: float, *, phase: int = 0
) -> _CellPlan:
    """A cell serving its own demand at a steady rate.

    `phase` rotates the demand shape. Neighbours given different phases have
    genuinely uncorrelated demand and are refused on the correlation rule;
    neighbours sharing a phase co-move and are judged on the remaining rules
    instead, which is how the fixture exercises more than one refusal path.
    """
    demand = tuple(
        level * _STEADY_SHAPE[(index + phase) % len(_STEADY_SHAPE)]
        for index in range(PERIOD_COUNT)
    )
    absorbed = tuple(value * _STEADY_ABSORPTION_RATIO for value in demand)
    return _CellPlan(cell_id, city, district, demand, absorbed, (3,) * PERIOD_COUNT)


def build_evidence_repository(
    *,
    tenant_id: str = TENANT_ID,
    pair_store_counts: tuple[tuple[int, ...], tuple[int, ...]] | None = None,
) -> InMemoryMergeSplitEvidenceRepository:
    """A fresh repository holding history that clears every readiness gate.

    `pair_store_counts` reshapes the merge pair's store history so a test can
    remove one signal at a time and watch the corresponding rule refuse.
    """
    return populate_evidence_repository(
        InMemoryMergeSplitEvidenceRepository(),
        tenant_id=tenant_id,
        pair_store_counts=pair_store_counts,
    )


def populate_evidence_repository(
    repository: InMemoryMergeSplitEvidenceRepository,
    *,
    tenant_id: str = TENANT_ID,
    pair_store_counts: tuple[tuple[int, ...], tuple[int, ...]] | None = None,
) -> InMemoryMergeSplitEvidenceRepository:
    """Write the history into an existing repository, e.g. a bundle's own."""
    bounds = periods()

    left_plan, right_plan = _merge_pair_plans(*(pair_store_counts or ()))
    plans: list[_CellPlan] = [left_plan, right_plan]

    # Two metropolitan clusters, sixteen cells each, chained so the graph
    # carries the thirty candidate adjacent pairs the sample-size gate wants.
    # Taipei's remaining cells each get their own demand rhythm; Kaohsiung's
    # share one rhythm but step down steeply in level, so the two clusters fail
    # the merge rules for different, real reasons.
    for index in range(16):
        cell_id = f"cell-taipei-{index:02d}"
        if cell_id in (MERGE_LEFT, MERGE_RIGHT):
            continue
        plans.append(
            _steady_plan(cell_id, "Taipei", "Xinyi", 900.0 - index * 20.0, phase=index)
        )
    for index in range(16):
        plans.append(
            _steady_plan(
                f"cell-kaohsiung-{index:02d}",
                "Kaohsiung",
                "Lingya",
                900.0 * (0.75**index),
            )
        )

    for plan in plans:
        repository.register_cell(
            tenant_id,
            CellRegistration(plan.cell_id, f"8a{plan.cell_id}", plan.city, plan.district),
        )
        for index, period_bounds in enumerate(bounds):
            repository.record_outcome(tenant_id, _outcome(plan, index, period_bounds))

    for prefix in ("cell-taipei", "cell-kaohsiung"):
        for index in range(15):
            repository.link_adjacent(
                tenant_id, f"{prefix}-{index:02d}", f"{prefix}-{index + 1:02d}"
            )

    return repository


def add_barrier_evidence(
    repository: InMemoryMergeSplitEvidenceRepository,
    *,
    tenant_id: str = TENANT_ID,
    heavy_side_multiple: float = 3.2,
) -> None:
    """Record side-labelled outcomes across a barrier inside the split zone.

    The two member cells sit on opposite sides of a recorded barrier, and the
    heavier side has been absorbing several times what the lighter side does.
    Without these rows the zone has no split evidence at all.
    """
    bounds = periods()
    plans = {
        SPLIT_LEFT: _steady_plan(SPLIT_LEFT, "Kaohsiung", "Lingya", 900.0),
        SPLIT_RIGHT: _steady_plan(SPLIT_RIGHT, "Kaohsiung", "Lingya", 900.0 * 0.75),
    }
    for cell_id, side, scale in (
        (SPLIT_LEFT, "A", heavy_side_multiple),
        (SPLIT_RIGHT, "B", 1.0),
    ):
        plan = plans[cell_id]
        for index, period_bounds in enumerate(bounds):
            repository.record_outcome(
                tenant_id,
                _outcome(plan, index, period_bounds, barrier_side=side, scale=scale),
            )


def matured_receipt(
    path: Path, *, eligible_count: int = 240, observed_count: int = 260
) -> Path:
    """Write a self-consistent inventory receipt showing a matured heat zone.

    The hash is computed rather than copied, so the loader's integrity check has
    something real to verify; `tamper` exists to prove it does.
    """
    payload: dict[str, object] = {
        "auto_seeded": False,
        "capabilities": {
            "avm": {
                "eligible_count": 0,
                "observed_count": 0,
                "relation": "model_ready.valuation_view",
                "view_version": "valuation-view-v1",
            },
            "heatzone": {
                "eligible_count": eligible_count,
                "observed_count": observed_count,
                "relation": "model_ready.heatzone_training_view",
                "view_version": "heatzone-training-view-v2",
            },
        },
        "inventory_version": "pg16-production-model-inventory-2027-04-01-v1",
        "kind": "pg16-model-ready-inventory-receipt",
        "observed_at": "2027-04-01T00:00:00Z",
        "schema_version": 1,
    }
    payload["integrity"] = {"content_sha256": compute_receipt_sha256(payload)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def tamper_eligible_count(path: Path, *, eligible_count: int) -> Path:
    """Raise the recorded label count without refreshing the integrity hash."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["capabilities"]["heatzone"]["eligible_count"] = eligible_count
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def use_matured_receipt(monkeypatch, tmp_path: Path) -> Path:
    """Point evidence assembly at a matured inventory receipt for one test.

    Only the file the loader opens is redirected; the loader itself, including
    its integrity and auto-seeded checks, still runs. There is no request-path
    equivalent of this seam -- production always reads the release-bound
    receipt, which is why the un-patched API tests abstain.
    """
    from modules.heatzone.application import merge_split_evidence

    receipt = matured_receipt(tmp_path / "matured-inventory-receipt.json")
    real_loader = merge_split_evidence.load_model_ready_receipt

    def _load(path: Path | None = None) -> dict[str, object]:
        return real_loader(path or receipt)

    monkeypatch.setattr(merge_split_evidence, "load_model_ready_receipt", _load)
    return receipt
