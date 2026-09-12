"""Server-side readers for HZ-004 absorption evidence (ODP-FR-HZ-006).

The merge/split engine may only read outcome history that a pipeline wrote, so
the repository surface here is deliberately read-only for the API: the write
methods exist for the absorption pipeline and for tests that stand in for it,
and nothing on the request path can reach them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from modules.heatzone.application.absorption_outcome_recorder import (
    AbsorptionOutcomeConflictError,
    AbsorptionOutcomeWriteError,
    UnregisteredCellError,
    measurement_differences,
)
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

    __slots__ = ("cell_id", "h3_index", "admin_city", "admin_district", "barrier_side", "barrier_description")

    def __init__(
        self,
        cell_id: str,
        h3_index: str,
        admin_city: str = "",
        admin_district: str = "",
        barrier_side: str | None = None,
        barrier_description: str = "",
    ) -> None:
        self.cell_id = cell_id
        self.h3_index = h3_index
        self.admin_city = admin_city
        self.admin_district = admin_district
        self.barrier_side = barrier_side
        self.barrier_description = barrier_description


class InMemoryMergeSplitEvidenceRepository:
    """In-memory reference implementation of `MergeSplitEvidenceRepository`."""

    def __init__(self) -> None:
        self._cells: dict[str, dict[str, CellRegistration]] = {}
        self._outcomes: dict[str, list[AbsorptionOutcomeRecord]] = {}
        self._adjacency: dict[str, set[tuple[str, str]]] = {}

    # -- pipeline-side writes -------------------------------------------------

    def register_cell(self, tenant_id: str, cell: CellRegistration) -> None:
        self._cells.setdefault(tenant_id, {})[cell.cell_id] = cell

    def get_cell(self, tenant_id: str, cell_id: str) -> CellRegistration | None:
        return self._cells.get(tenant_id, {}).get(cell_id)

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

    def append_absorption_outcome(
        self, tenant_id: str, outcome: AbsorptionOutcomeRecord
    ) -> AbsorptionOutcomeRecord:
        """`AbsorptionOutcomeWriter` surface over the same append-only history.

        Re-recording a period is a no-op when the stored row agrees and a
        refusal when it does not, matching the durable writer and the relation's
        append-only trigger; a fixture that quietly grew a second row for one
        period would let a test build a history production cannot.
        """
        registered = self._cells.get(tenant_id, {}).get(outcome.cell_id)
        if registered is None:
            # `geo_cell_id` is a foreign key into geo.h3_cells in PostgreSQL, so
            # an unregistered cell is refused there. Refusing here too keeps the
            # two from disagreeing -- and an outcome on a cell the reader cannot
            # join would otherwise be recorded and silently never read.
            raise UnregisteredCellError(
                f"cell '{outcome.cell_id}' is not a registered geo cell for tenant "
                f"'{tenant_id}'; HZ-004 outcomes attach to cells the geo pipeline "
                "published, not to identifiers a caller invents"
            )

        if outcome.barrier_side is not None:
            reg_side = getattr(registered, "barrier_side", None)
            if reg_side is None:
                raise AbsorptionOutcomeWriteError(
                    f"cell '{outcome.cell_id}' has no registered geo barrier; side-labelled outcomes require trusted geo evidence"
                )
            if outcome.barrier_side != reg_side:
                raise AbsorptionOutcomeWriteError(
                    f"barrier_side '{outcome.barrier_side}' does not match registered geo barrier side '{reg_side}'"
                )

        for existing in self._outcomes.get(tenant_id, []):
            if (
                existing.cell_id == outcome.cell_id
                and existing.period == outcome.period
                and existing.barrier_side == outcome.barrier_side
            ):
                differing = measurement_differences(existing, outcome)
                if differing:
                    raise AbsorptionOutcomeConflictError(
                        f"cell {outcome.cell_id} already holds a different recorded outcome "
                        f"for {outcome.period_start.isoformat()}.."
                        f"{outcome.period_end.isoformat()} (side={outcome.barrier_side}); "
                        f"differing: {sorted(differing)}. HZ-004 history is append-only"
                    )
                return existing
        self.record_outcome(tenant_id, outcome)
        return outcome

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
