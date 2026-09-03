"""Server-side readers for HZ-004 absorption evidence (ODP-FR-HZ-006).

The merge/split engine may only read outcome history that a pipeline wrote, so
the repository surface here is deliberately read-only for the API: the write
methods exist for the absorption pipeline and for tests that stand in for it,
and nothing on the request path can reach them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from modules.heatzone.application.merge_split_evidence import (
    AbsorptionOutcomeRecord,
    CellOutcomeSeries,
)

__all__ = [
    "CellRegistration",
    "InMemoryMergeSplitEvidenceRepository",
]


class CellRegistration:
    """Identity of an H3 cell as the geo pipeline registered it."""

    __slots__ = ("cell_id", "h3_index", "admin_city", "admin_district")

    def __init__(
        self,
        cell_id: str,
        h3_index: str,
        admin_city: str = "",
        admin_district: str = "",
    ) -> None:
        self.cell_id = cell_id
        self.h3_index = h3_index
        self.admin_city = admin_city
        self.admin_district = admin_district


class InMemoryMergeSplitEvidenceRepository:
    """In-memory reference implementation of `MergeSplitEvidenceRepository`."""

    def __init__(self) -> None:
        self._cells: dict[str, dict[str, CellRegistration]] = {}
        self._outcomes: dict[str, list[AbsorptionOutcomeRecord]] = {}
        self._adjacency: dict[str, set[tuple[str, str]]] = {}

    # -- pipeline-side writes -------------------------------------------------

    def register_cell(self, tenant_id: str, cell: CellRegistration) -> None:
        self._cells.setdefault(tenant_id, {})[cell.cell_id] = cell

    def record_outcome(self, tenant_id: str, outcome: AbsorptionOutcomeRecord) -> None:
        if not outcome.basis_source_ids:
            raise ValueError(
                f"absorption outcome for cell {outcome.cell_id} carries no basis "
                "snapshot ids; HZ-004 outcomes must be traceable to their source"
            )
        self._outcomes.setdefault(tenant_id, []).append(outcome)

    def record_outcomes(
        self, tenant_id: str, outcomes: Iterable[AbsorptionOutcomeRecord]
    ) -> None:
        for outcome in outcomes:
            self.record_outcome(tenant_id, outcome)

    def link_adjacent(self, tenant_id: str, left: str, right: str) -> None:
        if left == right:
            raise ValueError("a cell is not adjacent to itself")
        edge = (left, right) if left <= right else (right, left)
        self._adjacency.setdefault(tenant_id, set()).add(edge)

    # -- evidence reads -------------------------------------------------------

    def list_absorption_outcomes(self, tenant_id: str) -> list[AbsorptionOutcomeRecord]:
        return sorted(
            self._outcomes.get(tenant_id, []),
            key=lambda o: (o.cell_id, o.period_start, o.barrier_side or ""),
        )

    def list_cells(self, tenant_id: str) -> list[CellOutcomeSeries]:
        registrations = self._cells.get(tenant_id, {})
        outcomes = self.list_absorption_outcomes(tenant_id)
        return _assemble_series(registrations.values(), outcomes)

    def list_adjacency(self, tenant_id: str) -> list[tuple[str, str]]:
        return sorted(self._adjacency.get(tenant_id, set()))


def _assemble_series(
    registrations: Iterable[CellRegistration],
    outcomes: Sequence[AbsorptionOutcomeRecord],
) -> list[CellOutcomeSeries]:
    """Group outcomes onto their registered cells, whole-cell before side-split."""
    whole: dict[str, list[AbsorptionOutcomeRecord]] = {}
    sided: dict[str, list[AbsorptionOutcomeRecord]] = {}
    for outcome in outcomes:
        bucket = sided if outcome.barrier_side else whole
        bucket.setdefault(outcome.cell_id, []).append(outcome)

    series: list[CellOutcomeSeries] = []
    for registration in sorted(registrations, key=lambda c: c.cell_id):
        series.append(
            CellOutcomeSeries(
                cell_id=registration.cell_id,
                h3_index=registration.h3_index,
                admin_city=registration.admin_city,
                admin_district=registration.admin_district,
                outcomes=tuple(
                    sorted(
                        whole.get(registration.cell_id, []),
                        key=lambda o: o.period_start,
                    )
                ),
                side_outcomes=tuple(
                    sorted(
                        sided.get(registration.cell_id, []),
                        key=lambda o: (o.barrier_side or "", o.period_start),
                    )
                ),
            )
        )
    return series
