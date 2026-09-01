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
    fallback_keys: tuple[str, ...],
    what: str,
) -> bool:
    raw = policy.parameters.get(primary_key)
    if raw is None:
        for fb in fallback_keys:
            raw = policy.parameters.get(fb)
            if raw is not None:
                break
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
) -> list[AbsorbingStoreObservation]:
    """Assemble eligible AbsorbingStoreObservation DTOs according to strict refusal rules.

    Args:
        performances: Store daily performance records.
        operational_starts: Operational start observations by store_id or list.
        policy: The governing DecisionPolicy.
        target_store_ids: Optional set of store IDs to filter.

    Returns:
        List of eligible AbsorbingStoreObservation DTOs.
    """
    allow_declared = _required_bool(
        policy,
        ALLOW_DECLARED_START_KEY,
        ("allow_declared_operational_start", "allow_declared"),
        "admissibility of declared operational start",
    )
    allow_low_conf = _required_bool(
        policy,
        ALLOW_LOW_CONFIDENCE_START_KEY,
        ("allow_low_confidence_operational_start", "allow_low_confidence"),
        "admissibility of low-confidence operational start",
    )
    allow_unknown_conf = _required_bool(
        policy,
        ALLOW_UNKNOWN_CONFIDENCE_START_KEY,
        ("allow_unknown_confidence_operational_start", "allow_unknown_confidence"),
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

        # Refusal Rule 1: Coverage completeness check
        cov_state = (
            perf.coverage_state.value
            if hasattr(perf.coverage_state, "value")
            else str(perf.coverage_state).lower()
        )
        if cov_state != CoverageState.complete.value or not perf.is_complete:
            # Skip incomplete/partial/truncated day. Do not down-weight.
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

        b_date = _parse_date(perf.business_date)
        if b_date is None:
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
) -> AbsorptionResult | None:
    """Assemble observations and compute absorption for a single zone/cell.

    Returns:
        AbsorptionResult if valid eligible observations exist and compute_absorbed_demand succeeds,
        or None if no stores are present, no observations survive refusal rules, or all stores are
        inside the ramp window.

    Raises:
        AbsorptionInputError: If policy parameters are missing or invalid, or original_demand is invalid.
    """
    if original_demand < 0:
        raise AbsorptionInputError("original_demand must not be negative")

    eval_time = evaluated_at or datetime.now(UTC)

    target_store_ids = set(store_ids)
    if not target_store_ids:
        return None

    observations = assemble_absorbing_store_observations(
        performances,
        operational_starts,
        policy=policy,
        target_store_ids=target_store_ids,
    )

    if not observations:
        return None

    try:
        return compute_absorbed_demand(
            observations,
            original_demand=original_demand,
            policy=policy,
            as_of=as_of,
            evaluated_at=eval_time,
        )
    except AbsorptionInputError as exc:
        msg = str(exc)
        if "every observed store is inside" in msg or "no store observations" in msg or "cannot be measured" in msg:
            return None
        raise
