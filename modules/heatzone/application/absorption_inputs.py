"""Assembly of HeatZone demand absorption inputs from published contracts (ODP-FR-HZ-004).

This application-layer assembler bridges published foundation contracts:
- `oday.store-daily-performance.v1` (StoreDailyPerformance)
- `oday.operational-start-observation.v1` (OperationalStartObservation)
into domain DTO `AbsorbingStoreObservation` and evaluates demand absorption via
`compute_absorbed_demand`.

Refusal Rules Enforced:
1. Complete Coverage Only: `coverage_state` must be complete and `is_complete` must be True.
   Partial, truncated, or saturated store-days are skipped, never down-weighted.
2. Valid Zero vs Missing Revenue: If `paid_amount` is None, `is_valid_zero` must be True.
   Missing revenue without affirmative zero is skipped.
3. Definite Start Date Required: `observed_start_business_date` cannot be None.
   Without an operational start date, the store is refused entirely (ramp window cannot be evaluated).
   First-seen transaction date is never substituted.
4. Left-Censored Start Admissible: If `is_left_censored` is True, observation days is a lower bound,
   which is conservative and safe for ramp exclusion.
5. Policy-Governed Method and Confidence: Admissibility of `DECLARED` operational start and `LOW`
   or `UNKNOWN` confidence are governed by the DecisionPolicy with no code defaults.
6. Traceable Snapshot ID: `source_snapshot_id` comes from `raw_contract_fingerprint` of the source row.
7. Explicit Observation Window: absorption is measured only over a caller-supplied complete
   business-date window; missing days or paged subsets are refused rather than summed partially.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from modules.heatzone.v3.absorption import (
    MIN_OBSERVATION_DAYS_KEY,
    UNDER_REALIZED_RATIO_KEY,
    AbsorbingStoreObservation,
    AbsorptionInputError,
    AbsorptionNotMeasurableError,
    AbsorptionResult,
    compute_absorbed_demand,
)
from packages.oday_data_contracts_client.models.operational_start_observation import (
    OperationalStartConfidence,
    OperationalStartMethod,
    OperationalStartObservation,
)
from packages.oday_data_contracts_client.models.store_daily_performance import (
    CoverageState,
    StoreDailyPerformance,
)
from shared.governance import DecisionPolicy

__all__ = [
    "ALLOW_DECLARED_START_KEY",
    "ALLOW_LOW_CONFIDENCE_START_KEY",
    "ALLOW_UNKNOWN_CONFIDENCE_START_KEY",
    "MIN_OBSERVATION_DAYS_KEY",
    "UNDER_REALIZED_RATIO_KEY",
    "AbsorbingStoreObservation",
    "AbsorptionInputError",
    "AbsorptionNotMeasurableError",
    "AbsorptionResult",
    "assemble_absorbing_store_observations",
    "assemble_zone_absorption",
    "compute_absorbed_demand",
]

#: Policy key for admissibility of declared operational start dates.
ALLOW_DECLARED_START_KEY = "allow_declared_start"

#: Policy key for admissibility of low confidence operational start dates.
ALLOW_LOW_CONFIDENCE_START_KEY = "allow_low_confidence_start"

#: Policy key for admissibility of unknown confidence operational start dates.
ALLOW_UNKNOWN_CONFIDENCE_START_KEY = "allow_unknown_confidence_start"


def _required_bool(
    policy: DecisionPolicy,
    primary_key: str,
    what: str,
) -> bool:
    raw = policy.parameters.get(primary_key)
    if raw is None:
        raise AbsorptionInputError(
            f"policy {policy.policy_version_id} declares no {primary_key}; "
            f"{what} is a policy value and has no default here"
        )
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
    if isinstance(raw, (int, float)):
        return bool(raw)
    raise AbsorptionInputError(
        f"policy {policy.policy_version_id}: {primary_key}={raw!r} is not a boolean"
    )


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value).split("T")[0])
    except (ValueError, TypeError):
        return None


def assemble_absorbing_store_observations(
    performances: Sequence[StoreDailyPerformance | Mapping[str, Any]],
    operational_starts: (
        Mapping[str, OperationalStartObservation | Mapping[str, Any]]
        | Sequence[OperationalStartObservation | Mapping[str, Any]]
    ),
    *,
    policy: DecisionPolicy,
    target_store_ids: Sequence[str] | set[str] | None = None,
    observation_window_start: date | str | None = None,
    observation_window_end: date | str | None = None,
) -> list[AbsorbingStoreObservation]:
    """Assemble eligible AbsorbingStoreObservation DTOs according to strict refusal rules.

    Args:
        performances: Store daily performance records.
        operational_starts: Operational start observations by store_id or list.
        policy: The governing DecisionPolicy.
        target_store_ids: Optional set of store IDs to filter.
        observation_window_start: Inclusive evaluation-period start date.
        observation_window_end: Inclusive evaluation-period end date.

    Returns:
        List of eligible AbsorbingStoreObservation DTOs.
    """
    window = _parse_observation_window(observation_window_start, observation_window_end)

    allow_declared = _required_bool(
        policy,
        ALLOW_DECLARED_START_KEY,
        "admissibility of declared operational start",
    )
    allow_low_conf = _required_bool(
        policy,
        ALLOW_LOW_CONFIDENCE_START_KEY,
        "admissibility of low-confidence operational start",
    )
    allow_unknown_conf = _required_bool(
        policy,
        ALLOW_UNKNOWN_CONFIDENCE_START_KEY,
        "admissibility of unknown-confidence operational start",
    )

    # Build operational start lookup
    starts_map: dict[str, OperationalStartObservation] = {}
    if isinstance(operational_starts, Mapping):
        for s_id, op in operational_starts.items():
            if isinstance(op, Mapping):
                starts_map[str(s_id)] = OperationalStartObservation.from_dict(op)
            elif isinstance(op, OperationalStartObservation):
                starts_map[str(s_id)] = op
    else:
        for op in operational_starts:
            if isinstance(op, Mapping):
                op_obj = OperationalStartObservation.from_dict(op)
                starts_map[op_obj.store_id] = op_obj
            elif isinstance(op, OperationalStartObservation):
                starts_map[op.store_id] = op

    target_set = set(target_store_ids) if target_store_ids is not None else None
    observations: list[AbsorbingStoreObservation] = []

    for item in performances:
        if isinstance(item, Mapping):
            perf = StoreDailyPerformance.from_dict(item)
        else:
            perf = item

        store_id = perf.store_id
        if target_set is not None and store_id not in target_set:
            continue

        b_date = _parse_date(perf.business_date)
        if b_date is None:
            continue
        if window is not None and not window[0] <= b_date <= window[1]:
            continue

        # Refusal Rule 1: Coverage completeness check
        cov_state = (
            perf.coverage_state.value
            if hasattr(perf.coverage_state, "value")
            else str(perf.coverage_state).lower()
        )
        if cov_state not in (CoverageState.complete.value, CoverageState.empty.value) or not perf.is_complete:
            # Skip incomplete/partial/truncated day. Do not down-weight.
            continue
        if cov_state == CoverageState.empty.value and not perf.is_valid_zero:
            # EMPTY is affirmative zero evidence only when the producer says so.
            continue

        # Refusal Rule 2: Paid amount check
        if perf.paid_amount is None:
            if not perf.is_valid_zero:
                # Absent revenue without valid zero indication -> skip
                continue
            actual_revenue = 0.0
        else:
            try:
                actual_revenue = float(perf.paid_amount)
            except (TypeError, ValueError) as exc:
                raise AbsorptionInputError(
                    f"store {store_id} on {perf.business_date}: paid_amount={perf.paid_amount!r} is not a valid number"
                ) from exc
            if actual_revenue < 0:
                raise AbsorptionInputError(
                    f"store {store_id} on {perf.business_date}: negative actual revenue {actual_revenue}"
                )

        # Refusal Rule 3: Operational start check
        op_start = starts_map.get(store_id)
        if op_start is None:
            # Missing start observation -> refuse store
            continue
        opened_on = _parse_date(op_start.observed_start_business_date)
        if opened_on is None:
            # Missing observed start business date -> refuse store (cannot evaluate ramp)
            continue

        # Refusal Rule 5: Method & Confidence policy check
        method_str = (
            op_start.method.value
            if hasattr(op_start.method, "value")
            else str(op_start.method)
        ).upper()
        if method_str == OperationalStartMethod.DECLARED.value and not allow_declared:
            continue

        conf_str = (
            op_start.confidence.value
            if hasattr(op_start.confidence, "value")
            else str(op_start.confidence)
        ).upper()
        if conf_str == OperationalStartConfidence.LOW.value and not allow_low_conf:
            continue
        if conf_str == OperationalStartConfidence.UNKNOWN.value and not allow_unknown_conf:
            continue

        # Refusal Rule 4: Left-censored is kept (observation days is lower bound)
        # Refusal Rule 6: Source snapshot ID from raw_contract_fingerprint
        source_snapshot_id = perf.raw_contract_fingerprint
        if not source_snapshot_id or not str(source_snapshot_id).strip():
            continue

        observations.append(
            AbsorbingStoreObservation(
                store_id=store_id,
                business_date=b_date,
                actual_revenue=actual_revenue,
                opened_on=opened_on,
                source_snapshot_id=str(source_snapshot_id),
            )
        )

    return observations


def _parse_observation_window(
    observation_window_start: date | str | None,
    observation_window_end: date | str | None,
) -> tuple[date, date] | None:
    """Parse the caller's explicit demand/revenue evaluation period."""
    if observation_window_start is None and observation_window_end is None:
        return None
    start = _parse_date(observation_window_start)
    end = _parse_date(observation_window_end)
    if start is None or end is None:
        raise AbsorptionInputError(
            "observation window requires valid start and end business dates"
        )
    if start > end:
        raise AbsorptionInputError(
            "observation window start must not be after its end"
        )
    return start, end


def _retain_complete_store_windows(
    observations: Sequence[AbsorbingStoreObservation],
    window: tuple[date, date],
) -> list[AbsorbingStoreObservation]:
    """Keep only stores with one admitted observation for every active day.

    A caller may page through StoreDailyPerformance. Treating a partial page as
    a complete period would make absorption depend on page size, so a store is
    measured only when its requested period is fully represented by daily rows.
    """
    by_store: dict[str, list[AbsorbingStoreObservation]] = {}
    for observation in observations:
        by_store.setdefault(observation.store_id, []).append(observation)

    window_start, window_end = window
    complete: list[AbsorbingStoreObservation] = []
    for store_observations in by_store.values():
        dates = {observation.business_date for observation in store_observations}
        if len(dates) != len(store_observations):
            # Duplicate store-days must not be summed twice.
            continue
        opened_on = min(observation.opened_on for observation in store_observations)
        active_start = max(window_start, opened_on)
        if active_start > window_end:
            continue
        expected_dates = {
            date.fromordinal(day)
            for day in range(active_start.toordinal(), window_end.toordinal() + 1)
        }
        if not expected_dates.issubset(dates):
            continue
        complete.extend(
            observation
            for observation in store_observations
            if active_start <= observation.business_date <= window_end
        )
    return complete


def assemble_zone_absorption(
    *,
    store_ids: Sequence[str] | set[str],
    performances: Sequence[StoreDailyPerformance | Mapping[str, Any]],
    operational_starts: (
        Mapping[str, OperationalStartObservation | Mapping[str, Any]]
        | Sequence[OperationalStartObservation | Mapping[str, Any]]
    ),
    original_demand: float,
    policy: DecisionPolicy,
    as_of: date,
    evaluated_at: datetime | None = None,
    observation_window_start: date | str | None = None,
    observation_window_end: date | str | None = None,
) -> AbsorptionResult | None:
    """Assemble observations and compute absorption for a single zone/cell.

    Refusal Rules Enforced for Zone Completeness:
    - Every required store in store_ids must have an admissible operational start observation.
    - Every active store (opened on or before window end) must have exactly one complete,
      unambiguous StoreDailyPerformance record for every business date in its active
      observation window [max(window_start, opened_on), window_end].
    - If any required store has partial coverage, missing active dates (gaps), duplicate
      store-days, or fails refusal rules, the zone fails closed (returns None) to prevent
      partial observations from producing misleading UNDER_REALIZED or SATURATED signals.

    Returns:
        AbsorptionResult if all required stores have complete coverage and compute_absorbed_demand
        succeeds, or None if no stores are present, any required store has incomplete coverage,
        or all stores are inside the ramp window.

    Raises:
        AbsorptionInputError: If policy parameters are missing or invalid, or original_demand is invalid.
    """
    if original_demand < 0:
        raise AbsorptionInputError("original_demand must not be negative")

    window = _parse_observation_window(observation_window_start, observation_window_end)
    if window is None:
        # Demand has no period without an explicit evaluation window; refusing
        # is safer than making absorption depend on the caller's page size.
        return None

    target_store_ids = set(store_ids)
    if not target_store_ids:
        return None

    window_start, window_end = window

    allow_declared = _required_bool(
        policy,
        ALLOW_DECLARED_START_KEY,
        "admissibility of declared operational start",
    )
    allow_low_conf = _required_bool(
        policy,
        ALLOW_LOW_CONFIDENCE_START_KEY,
        "admissibility of low-confidence operational start",
    )
    allow_unknown_conf = _required_bool(
        policy,
        ALLOW_UNKNOWN_CONFIDENCE_START_KEY,
        "admissibility of unknown-confidence operational start",
    )

    # Build operational start lookup
    starts_map: dict[str, OperationalStartObservation] = {}
    if isinstance(operational_starts, Mapping):
        for s_id, op in operational_starts.items():
            if isinstance(op, Mapping):
                starts_map[str(s_id)] = OperationalStartObservation.from_dict(op)
            elif isinstance(op, OperationalStartObservation):
                starts_map[str(s_id)] = op
    else:
        for op in operational_starts:
            if isinstance(op, Mapping):
                op_obj = OperationalStartObservation.from_dict(op)
                starts_map[op_obj.store_id] = op_obj
            elif isinstance(op, OperationalStartObservation):
                starts_map[op.store_id] = op

    # Verify operational start and determine active observation window for all target stores
    active_stores: dict[str, tuple[date, date, date]] = {}
    excluded_stores: dict[str, str] = {}

    for store_id in sorted(target_store_ids):
        op_start = starts_map.get(store_id)
        if op_start is None:
            # Missing start observation -> active window cannot be verified -> fail closed
            return None
        opened_on = _parse_date(op_start.observed_start_business_date)
        if opened_on is None:
            # Missing observed start business date -> fail closed
            return None

        # Method & Confidence policy check
        method_str = (
            op_start.method.value
            if hasattr(op_start.method, "value")
            else str(op_start.method)
        ).upper()
        if method_str == OperationalStartMethod.DECLARED.value and not allow_declared:
            return None

        conf_str = (
            op_start.confidence.value
            if hasattr(op_start.confidence, "value")
            else str(op_start.confidence)
        ).upper()
        if conf_str == OperationalStartConfidence.LOW.value and not allow_low_conf:
            return None
        if conf_str == OperationalStartConfidence.UNKNOWN.value and not allow_unknown_conf:
            return None

        active_start = max(window_start, opened_on)
        if active_start > window_end:
            # Store opened after observation window ended
            excluded_stores[store_id] = "opened_after_observation_window"
        else:
            active_stores[store_id] = (active_start, window_end, opened_on)

    if not active_stores:
        return None

    # Index performance records by store_id and business_date
    store_perf_by_date: dict[str, dict[date, list[StoreDailyPerformance]]] = {}
    for item in performances:
        if isinstance(item, Mapping):
            perf = StoreDailyPerformance.from_dict(item)
        else:
            perf = item

        s_id = perf.store_id
        if s_id not in active_stores:
            continue

        b_date = _parse_date(perf.business_date)
        if b_date is None:
            continue

        act_start, act_end, _ = active_stores[s_id]
        if not (act_start <= b_date <= act_end):
            continue

        store_perf_by_date.setdefault(s_id, {}).setdefault(b_date, []).append(perf)

    # Verify coverage completeness for every required active store
    observations: list[AbsorbingStoreObservation] = []

    for s_id, (act_start, act_end, opened_on) in active_stores.items():
        expected_dates = [
            date.fromordinal(day)
            for day in range(act_start.toordinal(), act_end.toordinal() + 1)
        ]
        perf_map = store_perf_by_date.get(s_id, {})

        for exp_date in expected_dates:
            records = perf_map.get(exp_date)
            if not records or len(records) != 1:
                # Missing active day (gap) or duplicate store-day -> fail closed
                return None

            p = records[0]

            # Refusal Rule 1: Coverage completeness check
            cov_state = (
                p.coverage_state.value
                if hasattr(p.coverage_state, "value")
                else str(p.coverage_state).lower()
            )
            if cov_state not in (CoverageState.complete.value, CoverageState.empty.value) or not p.is_complete:
                # Incomplete coverage on active day -> fail closed
                return None
            if cov_state == CoverageState.empty.value and not p.is_valid_zero:
                # Empty without affirmative zero -> fail closed
                return None

            # Refusal Rule 2: Paid amount check
            if p.paid_amount is None:
                if not p.is_valid_zero:
                    return None
                actual_revenue = 0.0
            else:
                try:
                    actual_revenue = float(p.paid_amount)
                except (TypeError, ValueError) as exc:
                    raise AbsorptionInputError(
                        f"store {s_id} on {p.business_date}: paid_amount={p.paid_amount!r} is not a valid number"
                    ) from exc
                if actual_revenue < 0:
                    raise AbsorptionInputError(
                        f"store {s_id} on {p.business_date}: negative actual revenue {actual_revenue}"
                    )

            # Refusal Rule 6: Source snapshot ID from raw_contract_fingerprint
            source_snapshot_id = p.raw_contract_fingerprint
            if not source_snapshot_id or not str(source_snapshot_id).strip():
                return None

            observations.append(
                AbsorbingStoreObservation(
                    store_id=s_id,
                    business_date=exp_date,
                    actual_revenue=actual_revenue,
                    opened_on=opened_on,
                    source_snapshot_id=str(source_snapshot_id),
                )
            )

    eval_time = evaluated_at or datetime.now(UTC)

    try:
        return compute_absorbed_demand(
            observations,
            original_demand=original_demand,
            policy=policy,
            as_of=as_of,
            evaluated_at=eval_time,
            excluded_stores=excluded_stores,
        )
    except AbsorptionNotMeasurableError:
        return None
