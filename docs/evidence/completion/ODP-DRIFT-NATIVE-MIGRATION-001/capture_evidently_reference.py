"""Capture the pinned Evidently 0.7.21 drift behaviour as reference ground truth.

This harness executes the *released* drift engine
(``Report([DataDriftPreset(**preset_kwargs)]).run(current, reference)`` -- the
exact call ``EvidentlyDriftMonitor`` makes) over a fixed, synthetic case matrix
and records what it produced. The output file is the golden reference that
``tests/models/test_native_drift.py`` asserts the native engine against.

Nothing here is hand-written expected output: every recorded number comes from
executing Evidently 0.7.21.

Run it from the repository root, against the pinned lockfile::

    PYTHONPATH=. uv run --frozen --python 3.12 \
        python docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/capture_evidently_reference.py \
        docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/evidently_0_7_21_reference.json

It imports Evidently on purpose and is therefore only runnable while the
dependency is still installed; the golden it produced is committed so the
comparison survives the dependency removal.
"""

from __future__ import annotations

import json
import sys
import warnings
from datetime import UTC, datetime
from importlib.metadata import version

warnings.filterwarnings("ignore")

import pandas as pd
from evidently import Report
from evidently.presets import DataDriftPreset

from modules.learninghub.infrastructure.evidently_monitor import _drifted_column_names

NAN = float("nan")
INF = float("inf")


def _f(values):
    return [float(v) for v in values]


def cases():
    out = []

    def add(name, description, reference, current, preset=None, drift_share_threshold=0.5, dtypes=None):
        out.append(
            {
                "name": name,
                "description": description,
                "drift_share_threshold": drift_share_threshold,
                "preset_kwargs": dict(preset or {}),
                "dtypes": dict(dtypes or {}),
                "reference": reference,
                "current": current,
            }
        )

    # --- A. numeric dispatch, reference rows <= 1000 -------------------------
    add("num_float_ks_identical", "float, combined unique>5, n=100 -> ks, no drift",
        {"demand": _f(range(1, 101)), "rent": _f(range(101, 201))},
        {"demand": _f(range(1, 101)), "rent": _f(range(101, 201))})
    add("num_float_ks_shifted", "float, combined unique>5, n=100 -> ks, drift",
        {"demand": _f(range(1, 101))},
        {"demand": _f(range(1001, 1101))})
    add("num_float_combined_unique_2", "float, combined unique==2 -> z",
        {"value": _f([0, 1] * 50)},
        {"value": _f([0, 1] * 50)})
    add("num_float_combined_unique_2_shifted", "float, combined unique==2, proportions shifted -> z",
        {"value": _f([0] * 90 + [1] * 10)},
        {"value": _f([0] * 10 + [1] * 90)})
    add("num_float_combined_unique_3", "float, combined unique==3 -> chisquare",
        {"value": _f([0, 1, 2] * 33 + [0])},
        {"value": _f([0, 1, 2] * 33 + [2])})
    add("num_float_combined_unique_5", "float, combined unique==5 -> chisquare",
        {"value": _f([0, 1, 2, 3, 4] * 20)},
        {"value": _f([0, 1, 2, 3, 4] * 20)})
    add("num_float_combined_unique_6", "float, combined unique==6 -> ks (n<=1000)",
        {"value": _f([0, 1, 2, 3, 4, 5] * 20)},
        {"value": _f([0, 1, 2, 3, 4, 5] * 20)})
    add("num_float_combined_unique_6_split", "combined unique 6 from ref{0..2}+cur{3..5}",
        {"value": _f([0, 1, 2] * 33 + [0])},
        {"value": _f([3, 4, 5] * 33 + [3])})
    add("num_int_unique_6", "int dtype, current nunique 6 (<=10) -> new-API categorical vs legacy numerical",
        {"value": [0, 1, 2, 3, 4, 5] * 20},
        {"value": [0, 1, 2, 3, 4, 5] * 20})
    add("num_int_unique_11", "int dtype, current nunique 11 (>10) -> numerical on both paths",
        {"value": list(range(11)) * 10},
        {"value": list(range(11)) * 10})
    add("num_int_unique_2", "int dtype, nunique 2 -> categorical(new)/numerical(legacy), both dispatch z",
        {"value": [0, 1] * 50},
        {"value": [0, 1] * 50})

    # --- B. reference-length boundary (cleaned length) ----------------------
    add("num_float_n1000_ks", "cleaned reference length exactly 1000 -> ks",
        {"value": _f(range(1000))},
        {"value": _f(range(1000))})
    add("num_float_n1001_wasserstein", "cleaned reference length 1001 -> wasserstein",
        {"value": _f(range(1001))},
        {"value": _f(range(1001))})
    add("num_float_n1001_wasserstein_shifted", "cleaned reference length 1001, shifted -> wasserstein drift",
        {"value": _f(range(1001))},
        {"value": _f(range(500, 1501))})
    add("num_float_n1001_unique_5_js", "n=1001, combined unique 5 -> jensenshannon",
        {"value": _f(([0, 1, 2, 3, 4] * 201)[:1001])},
        {"value": _f(([0, 1, 2, 3, 4] * 201)[:1001])})
    add("num_float_n1001_unique_2_js", "n=1001, combined unique 2 -> jensenshannon (no z above 1000)",
        {"value": _f(([0, 1] * 501)[:1001])},
        {"value": _f(([0, 1] * 501)[:1001])})
    add("num_float_n1001_unique_6_wasserstein", "n=1001, combined unique 6 -> wasserstein",
        {"value": _f(([0, 1, 2, 3, 4, 5] * 168)[:1001])},
        {"value": _f(([0, 1, 2, 3, 4, 5] * 168)[:1001])})
    add("num_float_n1001_nan_cleans_to_1000", "1001 reference rows with 1 NaN -> cleaned 1000 -> ks not wasserstein",
        {"value": _f(range(1000)) + [NAN]},
        {"value": _f(range(1001))})
    add("num_float_n1001_inf_only_reference", "1001 reference rows with 1 +inf and no NaN",
        {"value": _f(range(1000)) + [INF]},
        {"value": _f(range(1001))})

    # --- C. categorical ------------------------------------------------------
    add("cat_str_unique_3", "object strings, low cardinality, combined unique 3 -> chisquare",
        {"region": ["north", "south", "east"] * 33 + ["north"]},
        {"region": ["north", "south", "east"] * 33 + ["east"]})
    add("cat_str_unique_2", "object strings, combined unique 2 -> z",
        {"flag": ["yes", "no"] * 50},
        {"flag": ["yes", "no"] * 50})
    add("cat_str_new_category", "current introduces an unseen category (zero expected frequency)",
        {"region": ["north", "south"] * 50},
        {"region": (["north", "south", "west"] * 34)[:100]})
    add("cat_bool", "bool dtype -> categorical, combined unique 2 -> z",
        {"active": [True, False] * 50},
        {"active": [True] * 90 + [False] * 10})
    add("cat_str_n1001_js", "object strings, reference length 1001 -> jensenshannon",
        {"region": (["north", "south", "east"] * 334)[:1001]},
        {"region": (["north", "south", "east"] * 334)[:1001]})

    # --- D. constant columns -------------------------------------------------
    add("constant_identical", "constant reference and current with the same value -> z short-circuit p=1",
        {"value": _f([5] * 100)},
        {"value": _f([5] * 100)})
    add("constant_different", "constant reference and current with different values -> z",
        {"value": _f([5] * 100)},
        {"value": _f([7] * 100)})
    add("constant_n1001_identical", "constant, reference length 1001 -> jensenshannon",
        {"value": _f([5] * 1001)},
        {"value": _f([5] * 1001)})
    add("constant_n1001_different", "constant but different, reference length 1001 -> jensenshannon",
        {"value": _f([5] * 1001)},
        {"value": _f([7] * 1001)})
    add("constant_reference_wasserstein_norm_floor", "constant reference with >5 combined unique above 1000 rows",
        {"value": _f([5] * 1001)},
        {"value": _f(range(1001))})

    # --- E. NaN / inf --------------------------------------------------------
    add("nan_in_both", "NaN present in both frames -> dropped before the stattest",
        {"value": _f(range(99)) + [NAN]},
        {"value": _f(range(99)) + [NAN]})
    add("inf_in_current", "+/-inf present in current -> replaced by NaN then dropped",
        {"value": _f(range(100))},
        {"value": _f(range(98)) + [INF, -INF]})
    add("nan_all_reference", "reference column is entirely NaN -> ValueError",
        {"value": [NAN] * 100},
        {"value": _f(range(100))})
    add("nan_all_current", "current column is entirely NaN -> ValueError",
        {"value": _f(range(100))},
        {"value": [NAN] * 100})

    # --- F. text -------------------------------------------------------------
    add("text_high_cardinality_n100", "object strings with nunique > 0.5*count -> text, n<=1000 -> perc_text_content_drift",
        {"note": [f"reference note number {i} about rent" for i in range(100)]},
        {"note": [f"reference note number {i} about rent" for i in range(100)]})
    add("text_high_cardinality_n100_shifted", "text drift with disjoint vocabulary",
        {"note": [f"reference note number {i} about rent" for i in range(100)]},
        {"note": [f"totally different subject {i} concerning weather" for i in range(100)]})
    add("text_high_cardinality_n1001", "text, reference length 1001 -> abs_text_content_drift",
        {"note": [f"reference note number {i} about rent" for i in range(1001)]},
        {"note": [f"reference note number {i} about rent" for i in range(1001)]})

    # --- G. drift-share threshold boundary ----------------------------------
    add("share_equals_threshold", "1 of 2 columns drifts, share == drift_share threshold",
        {"a": _f(range(100)), "b": _f(range(100))},
        {"a": _f(range(100)), "b": _f(range(1000, 1100))},
        drift_share_threshold=0.5)
    add("share_below_threshold", "1 of 4 columns drifts, share below threshold",
        {"a": _f(range(100)), "b": _f(range(100)), "c": _f(range(100)), "d": _f(range(100))},
        {"a": _f(range(100)), "b": _f(range(100)), "c": _f(range(100)), "d": _f(range(1000, 1100))},
        drift_share_threshold=0.5)
    add("share_threshold_one", "all columns drift, threshold 1.0",
        {"a": _f(range(100)), "b": _f(range(100))},
        {"a": _f(range(1000, 1100)), "b": _f(range(1000, 1100))},
        drift_share_threshold=1.0)

    # --- H. mixed frames / share denominator --------------------------------
    add("mixed_num_cat_text", "numeric + categorical + text in one frame",
        {"demand": _f(range(100)), "region": ["north", "south"] * 50,
         "note": [f"reference note number {i} about rent" for i in range(100)]},
        {"demand": _f(range(100)), "region": ["north", "south"] * 50,
         "note": [f"reference note number {i} about rent" for i in range(100)]})
    add("mixed_with_datetime_column", "datetime column is excluded from drift columns",
        {"demand": _f(range(100)), "stamp": [f"2026-01-{(i % 28) + 1:02d}" for i in range(100)]},
        {"demand": _f(range(100)), "stamp": [f"2026-01-{(i % 28) + 1:02d}" for i in range(100)]})

    # --- I. explicit threshold resolution (engine-level API) ----------------
    add("explicit_threshold_all", "explicit threshold for every column",
        {"demand": _f(range(100))},
        {"demand": _f(range(100))},
        preset={"threshold": 0.2})
    add("explicit_num_threshold", "explicit numerical threshold",
        {"demand": _f(range(1001))},
        {"demand": _f(range(500, 1501))},
        preset={"num_threshold": 0.9})
    add("explicit_per_column_threshold", "per-column threshold overrides the type default",
        {"demand": _f(range(100)), "rent": _f(range(100))},
        {"demand": _f(range(100)), "rent": _f(range(100))},
        preset={"per_column_threshold": {"demand": 0.9}})
    add("explicit_method_wasserstein", "explicit method overrides auto dispatch",
        {"demand": _f(range(100))},
        {"demand": _f(range(100))},
        preset={"method": "wasserstein"})
    add("explicit_method_jensenshannon", "explicit jensenshannon on a small numeric frame",
        {"demand": _f(range(100))},
        {"demand": _f(range(100))},
        preset={"method": "jensenshannon"})
    add("explicit_method_ks_above_1000", "explicit ks above the 1000-row boundary",
        {"demand": _f(range(1001))},
        {"demand": _f(range(1001))},
        preset={"method": "ks"})

    # --- J. two-path dispatch divergence ------------------------------------
    add("int_symmetric_frequency_swap",
        "int col, current nunique 6: new-API categorical->chisquare vs legacy numerical->ks",
        {"value": [0] * 10 + [1] * 30 + [2] * 20 + [3] * 20 + [4] * 30 + [5] * 10},
        {"value": [0] * 30 + [1] * 10 + [2] * 20 + [3] * 20 + [4] * 10 + [5] * 30})

    # --- K. unknown-typed column (share denominator) ------------------------
    add("unknown_object_column",
        "object column whose first/last values are not str -> Unknown for ValueDrift, categorical for the legacy count",
        {"mixed": [None] + [1, "a"] * 49 + [None], "demand": _f(range(100))},
        {"mixed": [None] + [1, "a"] * 49 + [None], "demand": _f(range(100))})

    # --- L. real datetime dtype ---------------------------------------------
    add("real_datetime_column",
        "datetime64 column is excluded from both the ValueDrift list and the legacy count columns",
        {"stamp": [f"2026-01-{(i % 28) + 1:02d}" for i in range(100)], "demand": _f(range(100))},
        {"stamp": [f"2026-01-{(i % 28) + 1:02d}" for i in range(100)], "demand": _f(range(100))},
        dtypes={"stamp": "datetime64[ns]"})

    # --- M. production-shaped frames (utility column names) -----------------
    add("prediction_column_numeric",
        "single float column literally named 'prediction' (production run_prediction shape)",
        {"prediction": _f(range(1, 101))},
        {"prediction": _f(range(1, 101))})
    add("prediction_column_numeric_shifted",
        "single float 'prediction' column, shifted (production alerting shape)",
        {"prediction": _f(range(1, 101))},
        {"prediction": _f(range(1001, 1101))})
    add("prediction_column_two_unique",
        "float 'prediction' column with 2 combined unique values above 1.0",
        {"prediction": _f([5, 7] * 50)},
        {"prediction": _f([5, 7] * 50)})
    add("prediction_column_probability",
        "float 'prediction' column bounded in [0, 1] with 4 combined unique values",
        {"prediction": _f([0.0, 0.25, 0.5, 0.75] * 25)},
        {"prediction": _f([0.0, 0.25, 0.5, 0.75] * 25)})
    add("prediction_column_string_labels",
        "low-cardinality string 'prediction' column (classification outputs)",
        {"prediction": ["approve", "reject"] * 50},
        {"prediction": ["approve", "reject"] * 50})
    add("prediction_column_high_cardinality_text",
        "high-cardinality string 'prediction' column: ValueDrift sees Text, legacy count sees categorical",
        {"prediction": [f"listing-{i} recommended for tenant" for i in range(100)]},
        {"prediction": [f"listing-{i} recommended for tenant" for i in range(100)]})
    add("target_and_prediction_columns",
        "frame carrying both 'target' and 'prediction' utility column names",
        {"target": _f(range(100)), "prediction": _f(range(100)), "demand": _f(range(100))},
        {"target": _f(range(100)), "prediction": _f(range(100)), "demand": _f(range(100))})

    # --- N. type-inference boundaries ---------------------------------------
    add("order_cat_before_num", "categorical column declared before the numeric one",
        {"region": ["north", "south"] * 50, "demand": _f(range(100))},
        {"region": ["north", "south"] * 50, "demand": _f(range(100))})
    add("int_unique_10", "int dtype with exactly 10 unique values -> categorical",
        {"value": list(range(10)) * 10},
        {"value": list(range(10)) * 10})
    add("str_unique_exactly_half", "object strings with nunique == 0.5*count -> categorical, not text",
        {"note": [f"n{i}" for i in range(50)] * 2},
        {"note": [f"n{i}" for i in range(50)] * 2})
    add("str_unique_just_over_half", "object strings with nunique == 0.5*count + 1 -> text",
        {"note": [f"n{i}" for i in range(51)] + [f"n{i}" for i in range(49)]},
        {"note": [f"n{i}" for i in range(51)] + [f"n{i}" for i in range(49)]})

    # --- O. cross-frame dtype and inf divergences ---------------------------
    add("dtype_mismatch_between_frames",
        "numeric in current but object in reference -> dropped from the legacy drift columns",
        {"value": [f"{i}" for i in range(100)]},
        {"value": _f(range(100))})
    add("inf_reference_shifted_divergence",
        "inf-only reference: the legacy count path skips cleaning while ValueDrift cleans",
        {"value": _f(range(1000)) + [INF]},
        {"value": _f(range(5000, 6001))})

    # --- P. sample-size regime and category removal --------------------------
    add("num_float_n200_ks", "200 reference rows, combined unique > 5 -> ks",
        {"demand": _f(range(200))},
        {"demand": _f(range(200))})
    add("cat_str_dropped_category", "a category present in reference disappears from current",
        {"region": (["north", "south", "west"] * 34)[:100]},
        {"region": ["north", "south"] * 50})

    return out


def _frame(columns: dict, dtypes: dict) -> pd.DataFrame:
    frame = pd.DataFrame(columns)
    for column, dtype in dtypes.items():
        frame[column] = frame[column].astype(dtype)
    return frame


def run_case(case: dict) -> dict:
    reference = _frame(case["reference"], case.get("dtypes") or {})
    current = _frame(case["current"], case.get("dtypes") or {})
    preset_kwargs = dict(case["preset_kwargs"])
    preset_kwargs.setdefault("drift_share", case["drift_share_threshold"])
    record: dict = {
        "name": case["name"],
        "description": case["description"],
        "drift_share_threshold": case["drift_share_threshold"],
        "preset_kwargs": case["preset_kwargs"],
        "dtypes": case.get("dtypes") or {},
        "reference": case["reference"],
        "current": case["current"],
        "reference_dtypes": {c: str(t) for c, t in reference.dtypes.items()},
        "current_dtypes": {c: str(t) for c, t in current.dtypes.items()},
    }
    try:
        evaluation = Report([DataDriftPreset(**preset_kwargs)]).run(current, reference)
    except Exception as exc:  # noqa: BLE001 - failure modes are part of the reference
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        record["report"] = None
        record["derived"] = None
        return record

    payload = json.loads(evaluation.json())
    summary = next(
        (
            metric.get("value", {})
            for metric in payload.get("metrics", [])
            if metric.get("metric_name", "").startswith("DriftedColumnsCount")
        ),
        {},
    )
    share = float(summary.get("share", 0.0) or 0.0)
    record["error"] = None
    record["report"] = payload
    # Mirrors EvidentlyDriftMonitor._result so the golden also pins the fields
    # the production consumers actually read.
    record["derived"] = {
        "drifted_columns": int(summary.get("count", 0) or 0),
        "drift_share": share,
        "drift_detected": share >= case["drift_share_threshold"],
        "drifted_column_names": list(_drifted_column_names(payload)),
    }
    return record


def main() -> int:
    results = []
    for case in cases():
        results.append(run_case(case))
        print(f"captured {case['name']}", file=sys.stderr)

    header = {
        "schema": "odp.drift.reference/1",
        "engine": "evidently",
        "entrypoint": "Report([DataDriftPreset(**preset_kwargs)]).run(current, reference)",
        "engine_version": version("evidently"),
        "scipy_version": version("scipy"),
        "numpy_version": version("numpy"),
        "pandas_version": version("pandas"),
        "scikit_learn_version": version("scikit-learn"),
        "python_version": sys.version.split()[0],
        "captured_at": datetime.now(UTC).isoformat(),
        "captured_by": "docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/capture_evidently_reference.py",
    }
    out = sys.argv[1]
    # One case per line: the file is machine-generated and large, but stays
    # greppable by case name.
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("{\n")
        for key, value in header.items():
            handle.write(f" {json.dumps(key)}: {json.dumps(value)},\n")
        handle.write(' "cases": [\n')
        for index, record in enumerate(results):
            suffix = "," if index + 1 < len(results) else ""
            handle.write(f"  {json.dumps(record, separators=(',', ':'))}{suffix}\n")
        handle.write(" ]\n}\n")
    print(f"wrote {out} ({len(results)} cases)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
