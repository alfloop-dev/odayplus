"""Answer acceptance criterion 5 by running the registry's own training code.

Motivation. Four of this task's five acceptance criteria have receipts. The
fifth -- "ODP-PRODUCTION-MODEL-REGISTRY-001 can resume training" -- had none,
and every measurement in this evidence directory answers a *different*
question: how many days landed, how many stores are attested, how many stores
carry 28 consecutive eligible dates. None of them asks whether the thing that
consumes the view can actually start. Those are not the same question, because
the consumer applies gates the coverage probes never model.

The decisive one was found by reading `modules/forecastops/model_contract.py`:

    FORECASTOPS_HORIZON_WEEKS = (4, 8, 12, 24)

The training path does not train on daily rows. `prepare_model_rows` routes
`forecastops` through `expand_forecast_horizon_rows`, which turns each daily row
into one horizon-average target per horizon whose full window is present. The
**shortest** window it will build is 4 weeks = 28 days, and it requires those 28
dates to be consecutive and present *after* the view's own eligibility filter.
So the h7 and h14 columns this task has been reporting are not inputs to
training at all -- h28 is the first horizon the model contract can use, which is
why `verify` is run with `--horizons 7,14,28,56,84,168` rather than 7,14,28.
That agrees with the critical-path decision rather than disturbing it: `-b3` was
already chosen because it is the slice that moves h28, and h28 is exactly the
horizon the registry needs. This probe records the agreement as a measurement
instead of leaving it as a coincidence.

Method: import the code under test, do not restate it. The same rule that the
donor backtest followed. This probe does not reimplement the loader's WHERE
clause, the horizon expansion, or the row-level gates -- it constructs the real
`PostgresModelReadySource` against the live target, calls the real
`inventory()`/`load()` with the real `MODEL_SPECS["forecastops"]`, and hands the
result to the real `prepare_model_rows`. A restatement would drift from the
consumer the moment either side changed, and a criterion-5 receipt that drifts
is worse than no receipt, because it would claim the registry can start when it
cannot. The gates are therefore evaluated in the same order `train()` applies
them, and the first one that fails is reported as the blocker.

Scope, stated so the receipt is not over-read. This probe covers the gates that
depend on DATA AVAILABILITY, which is what this task is responsible for
delivering: relation inventory, eligible-row load, horizon expansion, the
row-level lineage/temporal rejections, `minimum_rows`, the temporal split's
partition sizes, and `minimum_segment_rows` against the holdout. It stops before
`_temporal_validation`, which fits an actual regression and scores it against
`max_normalized_mae`/`min_p80_coverage`. That is a model-quality gate owned by
the registry task, it needs LightGBM, and it is not something a history backfill
can be said to pass or fail. Reporting a green light for a gate this probe did
not run would be the same mistake as the finisher's demoted measurements.

Read-only. Selects only; no writes, no DDL, no job mutation. Safe to run while a
backfill slice is in flight -- it touches the PG16 activation target, while the
slices write the PG15 source.

Usage:
    source /tmp/odp-forecast-dsn.env && python3 training-contract-readiness-probe.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import psycopg  # noqa: E402

from modules.forecastops.model_contract import (  # noqa: E402
    FORECASTOPS_HORIZON_WEEKS,
)
from scripts.models.contracts import MAX_TRAINING_ROWS, MODEL_SPECS, DataBounds  # noqa: E402
from scripts.models.release import (  # noqa: E402
    _temporal_split,
    prepare_model_rows,
)
from scripts.models.storage import (  # noqa: E402
    ModelReadyDataError,
    PostgresModelReadySource,
)

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.dirname(HERE)

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence-stage/training_contract_readiness.json",
)

# The view's own shape, measured independently of the loader so that a loader
# failure still leaves a description of what it was pointed at.
VIEW_STATE_SQL = """
SELECT
    count(*)                                                   AS rows_total,
    count(DISTINCT store_id)                                   AS stores_total,
    min(date)                                                  AS date_min,
    max(date)                                                  AS date_max,
    count(*) FILTER (WHERE is_training_eligible)               AS rows_eligible,
    count(DISTINCT store_id) FILTER (WHERE is_training_eligible) AS stores_eligible,
    min(date) FILTER (WHERE is_training_eligible)              AS date_min_eligible,
    max(date) FILTER (WHERE is_training_eligible)              AS date_max_eligible
FROM model_ready.forecast_training_view;
"""


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value.isoformat()


def _gate(name, status, detail, **extra):
    entry = {"gate": name, "status": status, "detail": detail}
    entry.update(extra)
    return entry


def main() -> int:
    target_dsn = os.environ.get("ODAY_DATABASE_URL")
    if not target_dsn:
        raise SystemExit("ODAY_DATABASE_URL is required; source /tmp/odp-forecast-dsn.env")

    spec = MODEL_SPECS["forecastops"]
    gates: list[dict] = []

    with psycopg.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute(VIEW_STATE_SQL)
        row = cur.fetchone()

    view_state = {
        "relation": spec.relation,
        "rows_total": int(row[0]),
        "stores_total": int(row[1]),
        "date_min": _iso(row[2]),
        "date_max": _iso(row[3]),
        "rows_training_eligible": int(row[4]),
        "stores_training_eligible": int(row[5]),
        "date_min_training_eligible": _iso(row[6]),
        "date_max_training_eligible": _iso(row[7]),
    }

    receipt = {
        "probe": "training-contract-readiness-probe.py",
        "generated_at": datetime.now(UTC).isoformat(),
        "question": (
            "Can ODP-PRODUCTION-MODEL-REGISTRY-001 resume training against the "
            "PG16 model-ready view as it currently stands?"
        ),
        "method": (
            "Runs the registry's own PostgresModelReadySource.inventory/load and "
            "release.prepare_model_rows against the live target; no rule is restated."
        ),
        "model_contract": {
            "horizon_weeks": list(FORECASTOPS_HORIZON_WEEKS),
            "shortest_horizon_days": min(FORECASTOPS_HORIZON_WEEKS) * 7,
            "minimum_rows": spec.minimum_rows,
            "minimum_segment_rows": spec.minimum_segment_rows,
            "holdout_fraction": spec.holdout_fraction,
            "note": (
                "The shortest window the training path builds is 28 days, so h7/h14 "
                "are not training inputs; h28 is the first usable horizon."
            ),
        },
        "view_state": view_state,
        "gates": gates,
    }

    if view_state["date_min_training_eligible"] is None:
        gates.append(
            _gate(
                "eligible_rows_exist",
                "FAIL",
                "the view holds no training-eligible rows at all",
            )
        )
        _write(receipt, blocking="eligible_rows_exist")
        return 1

    gates.append(
        _gate(
            "eligible_rows_exist",
            "PASS",
            f"{view_state['rows_training_eligible']} eligible rows over "
            f"{view_state['stores_training_eligible']} stores, "
            f"{view_state['date_min_training_eligible']}..{view_state['date_max_training_eligible']}",
        )
    )

    bounds = DataBounds(
        start=datetime.fromisoformat(view_state["date_min"]).replace(tzinfo=UTC),
        end=datetime.fromisoformat(view_state["date_max"]).replace(tzinfo=UTC)
        + timedelta(days=1),
        max_rows=MAX_TRAINING_ROWS,
    )
    receipt["bounds"] = {
        "start": bounds.start.isoformat(),
        "end": bounds.end.isoformat(),
        "max_rows": bounds.max_rows,
    }

    source = PostgresModelReadySource.from_database_url(target_dsn)

    inventory = source.inventory(spec)
    receipt["inventory"] = inventory.to_dict()
    if not inventory.ready:
        missing = ", ".join(inventory.missing_columns) or "eligible labeled rows"
        gates.append(
            _gate(
                "inventory_ready",
                "FAIL",
                inventory.blocked_reason or f"missing {missing}",
            )
        )
        _write(receipt, blocking="inventory_ready")
        return 1
    gates.append(
        _gate(
            "inventory_ready",
            "PASS",
            "the relation declares a model-ready contract and is trainable",
        )
    )

    try:
        loaded = source.load(spec, bounds)
    except ModelReadyDataError as exc:
        gates.append(_gate("loader_returns_rows", "FAIL", str(exc)))
        _write(receipt, blocking="loader_returns_rows")
        return 1
    gates.append(
        _gate(
            "loader_returns_rows",
            "PASS",
            f"{len(loaded.rows)} eligible labeled rows loaded inside bounds",
            rows_loaded=len(loaded.rows),
        )
    )

    # Horizon expansion is the gate this task's coverage probes never modelled.
    # It is also the one that raises rather than skips: a window missing lineage
    # or with inverted timestamps aborts the whole run instead of dropping a row.
    try:
        prepared = prepare_model_rows(spec, loaded)
    except ModelReadyDataError as exc:
        gates.append(_gate("horizon_expansion_and_row_gates", "FAIL", str(exc)))
        receipt["horizon_expansion"] = _safe_expansion_shape(spec, loaded)
        _write(receipt, blocking="horizon_expansion_and_row_gates")
        return 1

    expansion = _safe_expansion_shape(spec, loaded)
    receipt["horizon_expansion"] = expansion
    built = expansion.get("expanded_rows")
    gates.append(
        _gate(
            "horizon_expansion_and_row_gates",
            "PASS",
            (
                f"{built if built is not None else 'an undescribed number of'} "
                f"horizon rows built from {len(loaded.rows)} daily rows; lineage "
                "and temporal-order rejections all cleared"
            ),
        )
    )

    receipt["prepared_rows"] = {
        "count": len(prepared),
        "dropped_by_row_gates": (
            None if built is None else built - len(prepared)
        ),
        "distinct_segments": len({r.segment_value for r in prepared}),
    }

    if len(prepared) < spec.minimum_rows:
        gates.append(
            _gate(
                "minimum_rows",
                "FAIL",
                f"{len(prepared)} clean rows are below minimum {spec.minimum_rows}",
            )
        )
        _write(receipt, blocking="minimum_rows")
        return 1
    gates.append(
        _gate(
            "minimum_rows",
            "PASS",
            f"{len(prepared)} clean rows against minimum {spec.minimum_rows}",
        )
    )

    try:
        training_rows, holdout_rows = _temporal_split(
            prepared, holdout_fraction=spec.holdout_fraction
        )
    except ModelReadyDataError as exc:
        gates.append(_gate("temporal_split", "FAIL", str(exc)))
        _write(receipt, blocking="temporal_split")
        return 1
    gates.append(
        _gate(
            "temporal_split",
            "PASS",
            f"train={len(training_rows)} holdout={len(holdout_rows)}",
            train_rows=len(training_rows),
            holdout_rows=len(holdout_rows),
        )
    )

    # minimum_segment_rows is applied to the HOLDOUT, not the whole dataset:
    # _segment_validation skips any store with fewer than that many holdout rows
    # and fails outright if no store survives. A short eligible span can pass
    # minimum_rows and still die here.
    holdout_by_segment = Counter(r.segment_value for r in holdout_rows)
    qualifying = [c for c in holdout_by_segment.values() if c >= spec.minimum_segment_rows]
    receipt["segment_holdout"] = {
        "minimum_segment_rows": spec.minimum_segment_rows,
        "segments_in_holdout": len(holdout_by_segment),
        "segments_meeting_minimum": len(qualifying),
        "max_segment_holdout_rows": max(holdout_by_segment.values(), default=0),
    }
    if not qualifying:
        gates.append(
            _gate(
                "minimum_segment_rows",
                "FAIL",
                f"no {spec.segment_column} segment has at least "
                f"{spec.minimum_segment_rows} temporal holdout rows",
            )
        )
        _write(receipt, blocking="minimum_segment_rows")
        return 1
    gates.append(
        _gate(
            "minimum_segment_rows",
            "PASS",
            f"{len(qualifying)} of {len(holdout_by_segment)} segments carry at least "
            f"{spec.minimum_segment_rows} holdout rows",
        )
    )

    _write(receipt, blocking=None)
    return 0


def _safe_expansion_shape(spec, loaded) -> dict:
    """Describe the expansion without letting the description sink the receipt.

    `_expansion_shape` is a REPORTING path -- nothing it computes is a gate. The
    first run of this probe nevertheless lost 44 minutes of live measurement to
    it: `prepare_model_rows` had already failed with the finding the receipt
    exists to record, and the descriptive span calculation then raised
    `TypeError` and took the whole process down before `_write` could run. A
    measurement this expensive should degrade to a missing paragraph, never to a
    missing receipt, so any unexpected error here is captured as data instead.
    """
    try:
        return _expansion_shape(spec, loaded)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        return {
            "expanded_rows": None,
            "describe_error": f"{type(exc).__name__}: {exc}",
            "note": (
                "the expansion description failed; this is a reporting-path "
                "error and says nothing about the gates above, which were "
                "evaluated against the real loader"
            ),
            "per_horizon_weeks": {str(w): None for w in FORECASTOPS_HORIZON_WEEKS},
        }


def _as_date(value):
    """Coerce a loaded `date` cell to a real date.

    The loader hands `date` back as an ISO **string**, not a `datetime.date`.
    The first version of this probe subtracted them directly and died with
    `TypeError: unsupported operand type(s) for -: 'str' and 'str'` -- after a
    44-minute run, at the exact point the receipt was about to record the
    horizon-expansion failure, so the whole measurement was lost to a one-line
    type assumption in the *reporting* path rather than anything under test.
    Returns None for anything unparseable so a stray cell costs a span estimate
    rather than the receipt.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _expansion_shape(spec, loaded) -> dict:
    """Re-run the expansion alone to describe which horizons the data reaches.

    Reported separately from the pass/fail gate because it carries the finding
    that matters beyond this task: with a ~2-month span only the 4-week horizon
    can complete, so a resumed training run sees one value of the declared
    `horizon_weeks` feature.
    """
    from scripts.models.forecast_training import (
        ForecastHorizonContractError,
        expand_forecast_horizon_rows,
    )

    span_days = None
    dates = [_as_date(r.get("date")) for r in loaded.rows if r.get("date") is not None]
    dates = [d for d in dates if d is not None]
    if dates:
        span_days = (max(dates) - min(dates)).days + 1

    try:
        expanded = expand_forecast_horizon_rows(loaded.rows)
    except ForecastHorizonContractError as exc:
        return {
            "expanded_rows": 0,
            "error": str(exc),
            "eligible_span_days": span_days,
            "per_horizon_weeks": {str(w): 0 for w in FORECASTOPS_HORIZON_WEEKS},
        }

    per_horizon = Counter(int(r["horizon_weeks"]) for r in expanded)
    stores_per_horizon = defaultdict(set)
    for r in expanded:
        stores_per_horizon[int(r["horizon_weeks"])].add(str(r["store_id"]))

    return {
        "expanded_rows": len(expanded),
        "eligible_span_days": span_days,
        "per_horizon_weeks": {
            str(w): per_horizon.get(w, 0) for w in FORECASTOPS_HORIZON_WEEKS
        },
        "stores_per_horizon_weeks": {
            str(w): len(stores_per_horizon.get(w, ())) for w in FORECASTOPS_HORIZON_WEEKS
        },
        "horizons_unreachable": [
            w for w in FORECASTOPS_HORIZON_WEEKS if per_horizon.get(w, 0) == 0
        ],
    }


def _write(receipt: dict, *, blocking: str | None) -> None:
    passed = [g for g in receipt["gates"] if g["status"] == "PASS"]
    receipt["verdict"] = {
        "data_gates_passed": blocking is None,
        "blocking_gate": blocking,
        "gates_passed": len(passed),
        "gates_evaluated": len(receipt["gates"]),
        "not_evaluated": [
            "_temporal_validation (fits a regression; model-quality gate owned by "
            "ODP-PRODUCTION-MODEL-REGISTRY-001, not by this backfill)"
        ],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=False, default=str)
    print(json.dumps(receipt, indent=2, sort_keys=False, default=str))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    raise SystemExit(main())
