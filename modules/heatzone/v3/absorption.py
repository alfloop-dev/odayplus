"""Demand absorption from realised store performance (ODP-FR-HZ-004).

A heat zone's ranking is driven by unmet demand. Once a store opens inside the
zone and starts trading, part of that demand is being served -- and the zone
should stop being recommended as though nothing had happened. Without this the
same zone keeps ranking high after the first store opens, which produces
repeat recommendations, over-expansion, and a zone that contradicts the
cannibalisation figure SiteScore reports for the very same area
(`ODP-FR-SITE-003`).

Two rules carry the requirement.

**Absorption is computed from realised revenue, never from a prediction.**
Using SiteScore's forecast would make the loop self-fulfilling: a zone
predicted to perform well would be judged to have absorbed more, would drop in
rank, and that prediction would never be tested against anything. Only
observations carrying `actual_revenue` are accepted, and each one names the
snapshot it came from.

**Stores inside their ramp period are excluded.** A store trading for two weeks
has not yet absorbed what it will absorb; counting it understates absorption
and leaves the zone ranked too high. The minimum observation window is a policy
value (`ODP-SA-07` §8), not a constant here -- `min_observation_days` comes
from the governing `DecisionPolicy`, so it can be changed and versioned without
a deploy.

The same policy carries `under_realized_ratio`: the share of the zone's demand
an open, past-ramp store network is expected to be taking. Below it the zone is
not saturated and not expandable -- it is a place we already opened in that is
not working, which is a different decision from either.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from shared.governance import DecisionPolicy

__all__ = [
    "MIN_OBSERVATION_DAYS_KEY",
    "UNDER_REALIZED_RATIO_KEY",
    "AbsorbingStoreObservation",
    "AbsorptionInputError",
    "AbsorptionResult",
    "compute_absorbed_demand",
]

#: Policy key for the ramp exclusion window.
MIN_OBSERVATION_DAYS_KEY = "min_observation_days"

#: Policy key for the share of demand an open, past-ramp zone is expected to take.
UNDER_REALIZED_RATIO_KEY = "under_realized_ratio"


class AbsorptionInputError(ValueError):
    """The observations cannot support an absorption figure.

    Raised rather than returning a zero or a guess: a zone whose absorption
    cannot be computed must keep its previous standing and say so, not silently
    read as "nothing absorbed" -- which would rank it as though it were still
    untouched.
    """


@dataclass(frozen=True)
class AbsorbingStoreObservation:
    """One trading day of a store that sits inside the zone.

    This is a heat-zone-local DTO on purpose. ForecastOps has a similar shape
    (`StoreDayObservation`), but importing it here would couple two domains
    directly, which the repository's boundary rules forbid: cross-domain data
    travels as shared DTOs, events, or model-ready views. The application layer
    assembles these from whichever of those it reads.
    """

    store_id: str
    business_date: date
    actual_revenue: float
    opened_on: date
    source_snapshot_id: str

    def observation_days(self, as_of: date) -> int:
        """Days the store has been trading as of the evaluation date."""
        return (as_of - self.opened_on).days


@dataclass(frozen=True)
class AbsorptionResult:
    """What the zone has absorbed, and what is left.

    `remaining_demand` is what the ranking should now read. `absorbed_demand`
    and `absorption_ratio` are kept alongside it so a drop in rank can be
    explained -- and so a drop caused by absorption can be told apart from a
    drop caused by switching evidence source, which is why `basis_source_ids`
    and `basis_at` are part of the result rather than logged and forgotten.

    `under_realized` separates "this zone is served" from "we opened here and it
    is not working". Both leave little unmet demand; only the second calls for
    fixing the stores that are already there rather than ranking the zone down.
    """

    original_demand: float
    absorbed_demand: float
    remaining_demand: float
    absorption_ratio: float
    absorbing_store_count: int
    basis_at: datetime
    basis_source_ids: tuple[str, ...]
    excluded_store_ids: tuple[str, ...] = field(default_factory=tuple)
    under_realized: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "original_demand": self.original_demand,
            "absorbed_demand": self.absorbed_demand,
            "remaining_demand": self.remaining_demand,
            "absorption_ratio": self.absorption_ratio,
            "absorbing_store_count": self.absorbing_store_count,
            "under_realized": self.under_realized,
            "basis_at": self.basis_at.isoformat(),
            "basis_source_ids": list(self.basis_source_ids),
            "excluded_store_ids": list(self.excluded_store_ids),
        }


def _required(policy: DecisionPolicy, key: str, what: str) -> object:
    raw = policy.parameters.get(key)
    if raw is None:
        raise AbsorptionInputError(
            f"policy {policy.policy_version_id} declares no {key}; "
            f"{what} is a policy value and has no default here"
        )
    return raw


def _min_observation_days(policy: DecisionPolicy) -> int:
    raw = _required(policy, MIN_OBSERVATION_DAYS_KEY, "the ramp exclusion window")
    try:
        days = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise AbsorptionInputError(
            f"policy {policy.policy_version_id}: {MIN_OBSERVATION_DAYS_KEY}={raw!r} "
            "is not an integer number of days"
        ) from exc
    if days < 0:
        raise AbsorptionInputError(
            f"policy {policy.policy_version_id}: {MIN_OBSERVATION_DAYS_KEY} must not be negative"
        )
    return days


def _under_realized_ratio(policy: DecisionPolicy) -> float:
    raw = _required(
        policy,
        UNDER_REALIZED_RATIO_KEY,
        "the share of demand an open zone is expected to take",
    )
    try:
        ratio = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise AbsorptionInputError(
            f"policy {policy.policy_version_id}: {UNDER_REALIZED_RATIO_KEY}={raw!r} "
            "is not a number"
        ) from exc
    if not 0.0 <= ratio <= 1.0:
        raise AbsorptionInputError(
            f"policy {policy.policy_version_id}: {UNDER_REALIZED_RATIO_KEY} must be a "
            "share between 0 and 1"
        )
    return ratio


def compute_absorbed_demand(
    observations: Sequence[AbsorbingStoreObservation],
    *,
    original_demand: float,
    policy: DecisionPolicy,
    as_of: date,
    evaluated_at: datetime,
) -> AbsorptionResult:
    """Absorbed and remaining demand for one zone, from realised revenue only.

    `original_demand` is the zone's demand before any store served it -- the
    figure absorption is measured against. Passing an already-reduced number
    would compound the reduction on every run.

    Stores that have not yet traded for `min_observation_days` are excluded and
    named in the result. Absorption is capped at the original demand: a zone
    can be fully served, never over-served, and a ratio above 1 would make
    `remaining_demand` negative and the ranking meaningless.
    """
    if original_demand < 0:
        raise AbsorptionInputError("original_demand must not be negative")

    if not observations:
        # Distinct from "nothing absorbed": with no observations at all there is
        # no evidence either way, and the caller must not record a zero as if it
        # had been measured.
        raise AbsorptionInputError(
            "no store observations supplied; absorption cannot be measured without "
            "realised revenue"
        )

    eligible: list[AbsorbingStoreObservation] = []
    excluded: set[str] = set()
    min_days = _min_observation_days(policy)
    under_realized_ratio = _under_realized_ratio(policy)

    for obs in observations:
        if obs.actual_revenue < 0:
            raise AbsorptionInputError(
                f"store {obs.store_id} on {obs.business_date}: actual_revenue is negative"
            )
        if not obs.source_snapshot_id:
            raise AbsorptionInputError(
                f"store {obs.store_id} on {obs.business_date}: observation carries no "
                "source snapshot; absorption must be traceable to the data it came from"
            )
        if obs.observation_days(as_of) < min_days:
            excluded.add(obs.store_id)
            continue
        eligible.append(obs)

    if not eligible:
        raise AbsorptionInputError(
            f"every observed store is inside the {min_days}-day ramp window; "
            "absorption is not measurable yet for this zone"
        )

    absorbed = sum(obs.actual_revenue for obs in eligible)
    capped = min(absorbed, original_demand)
    remaining = original_demand - capped
    ratio = (capped / original_demand) if original_demand > 0 else 0.0

    return AbsorptionResult(
        original_demand=original_demand,
        absorbed_demand=capped,
        remaining_demand=remaining,
        absorption_ratio=ratio,
        absorbing_store_count=len({obs.store_id for obs in eligible}),
        basis_at=evaluated_at,
        basis_source_ids=tuple(sorted({obs.source_snapshot_id for obs in eligible})),
        excluded_store_ids=tuple(sorted(excluded)),
        under_realized=ratio < under_realized_ratio,
    )
