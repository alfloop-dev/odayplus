"""Forecast-specific expansion from daily model-ready rows to horizon targets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from statistics import fmean
from typing import Any

from modules.forecastops.model_contract import FORECASTOPS_HORIZON_WEEKS


class ForecastHorizonContractError(ValueError):
    """Raised when daily rows cannot produce leakage-safe horizon targets."""


def expand_forecast_horizon_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        tenant_id = str(row.get("tenant_id") or "").strip()
        store_id = str(row.get("store_id") or "").strip()
        if not tenant_id or not store_id:
            raise ForecastHorizonContractError(
                "forecast horizon training requires tenant_id and store_id"
            )
        grouped[(tenant_id, store_id)].append(row)

    expanded: list[dict[str, Any]] = []
    for (tenant_id, store_id), store_rows in sorted(grouped.items()):
        ordered = sorted(store_rows, key=lambda row: _date(row.get("date")))
        dates = [_date(row.get("date")) for row in ordered]
        for origin_index, origin in enumerate(ordered):
            origin_date = dates[origin_index]
            for horizon_weeks in FORECASTOPS_HORIZON_WEEKS:
                horizon_days = horizon_weeks * 7
                window = ordered[origin_index : origin_index + horizon_days]
                if len(window) != horizon_days:
                    continue
                expected_dates = [
                    origin_date + timedelta(days=offset) for offset in range(horizon_days)
                ]
                if [_date(row.get("date")) for row in window] != expected_dates:
                    continue
                revenues = [_finite_revenue(row.get("daily_net_revenue")) for row in window]
                label_maturity_time = max(
                    _datetime(row.get("label_maturity_time")) for row in window
                )
                feature_snapshot_time = _datetime(origin.get("feature_snapshot_time"))
                prediction_origin_time = _datetime(origin.get("prediction_origin_time"))
                if feature_snapshot_time >= prediction_origin_time:
                    raise ForecastHorizonContractError(
                        "feature_snapshot_time must precede prediction_origin_time"
                    )
                if label_maturity_time < prediction_origin_time:
                    raise ForecastHorizonContractError(
                        "horizon label maturity cannot precede prediction origin"
                    )
                window_source_ids: set[str] = set()
                for row in window:
                    row_source_ids = _source_ids(row.get("source_snapshot_ids"))
                    if not row_source_ids:
                        raise ForecastHorizonContractError(
                            "forecast horizon labels require source snapshot lineage"
                        )
                    window_source_ids.update(row_source_ids)
                source_snapshot_ids = sorted(window_source_ids)
                expanded.append(
                    {
                        **origin,
                        "entity_id": (
                            f"{tenant_id}:{store_id}:{origin_date.isoformat()}:w{horizon_weeks}"
                        ),
                        "feature_snapshot_time": feature_snapshot_time,
                        "prediction_origin_time": prediction_origin_time,
                        "label_maturity_time": label_maturity_time,
                        "daily_net_revenue": fmean(revenues),
                        "horizon_weeks": horizon_weeks,
                        "source_snapshot_ids": source_snapshot_ids,
                    }
                )
    if not expanded:
        raise ForecastHorizonContractError(
            "daily forecast rows do not contain a complete canonical horizon window"
        )
    return tuple(
        sorted(
            expanded,
            key=lambda row: (
                _date(row["date"]),
                str(row["tenant_id"]),
                str(row["store_id"]),
                int(row["horizon_weeks"]),
            ),
        )
    )


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        raise ForecastHorizonContractError("forecast daily row date is required")
    return date.fromisoformat(str(value))


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value in (None, ""):
        raise ForecastHorizonContractError("forecast horizon row requires timestamp lineage")
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _finite_revenue(value: Any) -> float:
    revenue = float(value)
    if revenue != revenue or revenue in {float("inf"), float("-inf")}:
        raise ForecastHorizonContractError("forecast horizon revenue must be finite")
    return revenue


def _source_ids(value: Any) -> tuple[str, ...]:
    values = (value,) if isinstance(value, str) else tuple(value or ())
    return tuple(str(item).strip() for item in values if str(item).strip())


__all__ = ["ForecastHorizonContractError", "expand_forecast_horizon_rows"]
