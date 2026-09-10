"""Recording of HZ-004 absorption outcomes as merge/split evidence (ODP-FR-HZ-006).

`compute_absorbed_demand` produces an `AbsorptionResult` and, until this module,
nothing kept it. Merge/split is required to reason only from realised absorption
history, so an outcome that is computed and discarded leaves the engine with a
relation it can read and nobody can fill: in production `evaluate` would abstain
forever on empty evidence, and the only histories that ever existed were the
ones tests wrote into the in-memory fixture directly.

Two properties make a recorded outcome admissible as evidence, and both are
enforced here rather than left to the caller:

*Source-bound.* The measured quantities are taken from the `AbsorptionResult`,
never from a caller's parameters, and the result must carry the
`basis_source_ids` that `assemble_zone_absorption` lifted from each source row's
`raw_contract_fingerprint`. An outcome with no basis is untraceable, and an
untraceable outcome is indistinguishable from one somebody typed in.

*Append-only.* A period is written once. A recomputation that agrees is a
no-op; one that disagrees is refused rather than allowed to overwrite, because
a merge decided last week was decided against the number that was there then,
and silently replacing it would make the decision unexplainable. PostgreSQL
holds the same rule with a trigger that rejects every UPDATE and DELETE on the
relation.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from modules.heatzone.application.merge_split_evidence import AbsorptionOutcomeRecord
from modules.heatzone.v3.absorption import AbsorptionResult
from shared.governance import HEATZONE_ABSORPTION_POLICY_KIND, DecisionPolicy

__all__ = [
    "MEASURED_FIELDS",
    "AbsorptionOutcomeConflictError",
    "AbsorptionOutcomeWriteError",
    "AbsorptionOutcomeWriter",
    "UnregisteredCellError",
    "build_absorption_outcome",
    "measurement_differences",
    "record_absorption_outcome",
]

#: The only sides a barrier can have. A split is only admissible where the geo
#: pipeline recorded a barrier, so the label set is closed rather than free text.
BARRIER_SIDES = ("A", "B")


class AbsorptionOutcomeWriteError(ValueError):
    """Raised when an outcome is not admissible as HZ-004 evidence."""


class AbsorptionOutcomeConflictError(AbsorptionOutcomeWriteError):
    """Raised when a period already holds a different recorded outcome."""


class UnregisteredCellError(AbsorptionOutcomeWriteError):
    """Raised when the outcome names a cell the geo pipeline never registered."""


#: The fields compared when a period is recorded again. `basis_at` is excluded
#: on purpose: it is when the computation ran, not what it measured, so a rerun
#: of the same inputs differs there and nowhere else. Including it would turn
#: every idempotent replay into a conflict.
MEASURED_FIELDS = (
    "original_demand",
    "absorbed_demand",
    "remaining_demand",
    "absorption_ratio",
    "absorbing_store_count",
    "under_realized",
    "barrier_description",
    "absorption_policy_version_id",
    "basis_source_ids",
)


def measurement_differences(
    existing: AbsorptionOutcomeRecord, incoming: AbsorptionOutcomeRecord
) -> list[str]:
    """Which measured fields two recordings of one period disagree on."""
    return [
        field
        for field in MEASURED_FIELDS
        if getattr(existing, field) != getattr(incoming, field)
    ]


class AbsorptionOutcomeWriter(Protocol):
    """The append-only sink for computed HZ-004 outcomes.

    Deliberately narrower than the evidence repository the merge/split API
    reads: the request path resolves a reader, so no route that evaluates or
    approves a composition can reach a writer for the evidence it is judged
    against.
    """

    def append_absorption_outcome(
        self, tenant_id: str, outcome: AbsorptionOutcomeRecord
    ) -> AbsorptionOutcomeRecord:
        ...


def build_absorption_outcome(
    *,
    cell_id: str,
    period_start: date,
    period_end: date,
    result: AbsorptionResult,
    policy: DecisionPolicy,
    barrier_side: str | None = None,
    barrier_description: str = "",
) -> AbsorptionOutcomeRecord:
    """Turn a computed `AbsorptionResult` into a persistable outcome row.

    Every number on the returned record comes off `result`; the parameters only
    say which cell, period and barrier side the computation was for.
    """
    if not str(cell_id).strip():
        raise AbsorptionOutcomeWriteError("an absorption outcome must name the cell it measured")

    if period_end < period_start:
        raise AbsorptionOutcomeWriteError(
            f"absorption period for cell {cell_id} ends ({period_end.isoformat()}) before it "
            f"starts ({period_start.isoformat()})"
        )

    if policy.policy_kind != HEATZONE_ABSORPTION_POLICY_KIND:
        raise AbsorptionOutcomeWriteError(
            f"policy {policy.policy_version_id} is of kind '{policy.policy_kind}', not "
            f"'{HEATZONE_ABSORPTION_POLICY_KIND}'; an outcome must name the policy that "
            "measured it, not the one that will judge it"
        )

    if not result.basis_source_ids:
        raise AbsorptionOutcomeWriteError(
            f"absorption outcome for cell {cell_id} over "
            f"{period_start.isoformat()}..{period_end.isoformat()} carries no basis snapshot "
            "ids; HZ-004 outcomes must be traceable to the source rows they were computed from"
        )

    if barrier_side is not None and barrier_side not in BARRIER_SIDES:
        raise AbsorptionOutcomeWriteError(
            f"barrier_side {barrier_side!r} is not one of {BARRIER_SIDES}"
        )

    if barrier_side is not None and not barrier_description.strip():
        raise AbsorptionOutcomeWriteError(
            f"side-labelled outcome for cell {cell_id} carries no barrier description; a split "
            "may only be taken on a barrier the geo pipeline recorded"
        )

    return AbsorptionOutcomeRecord(
        cell_id=str(cell_id),
        period_start=period_start,
        period_end=period_end,
        original_demand=result.original_demand,
        absorbed_demand=result.absorbed_demand,
        remaining_demand=result.remaining_demand,
        absorption_ratio=result.absorption_ratio,
        absorbing_store_count=result.absorbing_store_count,
        basis_source_ids=tuple(result.basis_source_ids),
        absorption_policy_version_id=policy.policy_version_id,
        basis_at=result.basis_at,
        under_realized=result.under_realized,
        barrier_side=barrier_side,
        barrier_description=barrier_description,
    )


def record_absorption_outcome(
    writer: AbsorptionOutcomeWriter,
    *,
    tenant_id: str,
    cell_id: str,
    period_start: date,
    period_end: date,
    result: AbsorptionResult,
    policy: DecisionPolicy,
    barrier_side: str | None = None,
    barrier_description: str = "",
) -> AbsorptionOutcomeRecord:
    """Append one computed HZ-004 outcome to the tenant's evidence history."""
    if not str(tenant_id).strip():
        raise AbsorptionOutcomeWriteError("an absorption outcome must name its tenant")

    if policy.tenant_id != tenant_id:
        raise AbsorptionOutcomeWriteError(
            f"policy {policy.policy_version_id} belongs to tenant '{policy.tenant_id}', not "
            f"'{tenant_id}'"
        )

    outcome = build_absorption_outcome(
        cell_id=cell_id,
        period_start=period_start,
        period_end=period_end,
        result=result,
        policy=policy,
        barrier_side=barrier_side,
        barrier_description=barrier_description,
    )
    return writer.append_absorption_outcome(tenant_id, outcome)
