"""Equivalence and behaviour tests for the native drift statistics core.

The bulk of this file replays a golden reference that was captured by
*executing* the pinned ``evidently==0.7.21`` engine over a fixed synthetic case
matrix (see
``docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/capture_evidently_reference.py``).
No expected value in the golden is hand-written.

Three divergences from that reference are deliberate. They are pinned
individually below rather than normalised away, so a future change to any of
them fails loudly.
"""

from __future__ import annotations

import ast
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from modules.learninghub.infrastructure import native_drift
from modules.learninghub.infrastructure.evidently_monitor import _drifted_column_names
from modules.learninghub.infrastructure.native_drift import (
    ABSOLUTE_TEXT_STAT_TEST,
    CHI_SQUARE_STAT_TEST,
    JENSEN_SHANNON_STAT_TEST,
    KS_STAT_TEST,
    METRIC_TYPE_PREFIX,
    PERCENTILE_TEXT_STAT_TEST,
    STAT_TESTS,
    WASSERSTEIN_STAT_TEST,
    Z_STAT_TEST,
    NativeColumnType,
    NativeDriftEngine,
    NativeDriftError,
    StatTestInvalidColumnTypeError,
    StatTestNotFoundError,
    default_stat_test,
    evaluate_drift,
    infer_column_type,
)

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_PATH = (
    ROOT
    / "docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/evidently_0_7_21_reference.json"
)
REFERENCE_METRIC_TYPE_PREFIX = "evidently:metric_v2:"

#: The reference raises ``ZeroDivisionError`` when column typing leaves the
#: drifted-column share without a denominator. The native engine raises a
#: diagnosable ``NativeDriftError`` (a ``ValueError``) instead.
KNOWN_ERROR_DIVERGENCES = {"dtype_mismatch_between_frames"}

#: ``EvidentlyDriftMonitor._drifted_column_names`` re-derives the drifted
#: columns by parsing the report's ``metric_name`` strings and guessing the
#: comparison direction from the method label. That heuristic is wrong for the
#: two cases below; the native engine reports the decision the drift test
#: actually made, which is also what ``DriftedColumnsCount`` counted.
KNOWN_DRIFTED_NAME_DIVERGENCES = {
    # Text drift is decided against a bootstrapped random-classifier
    # percentile, not against the reported threshold, so the parser reads
    # 0.941 < 0.95 as "no drift" while the count says one column drifted.
    "text_high_cardinality_n100_shifted": ([], ["note"]),
    # A caller-pinned method is echoed as the raw name ``ks``, which the
    # parser does not recognise as a p-value, so it applies the distance rule
    # and reports drift that the count did not see.
    "explicit_method_ks_above_1000": (["demand"], []),
}


def _load_reference() -> dict[str, Any]:
    with REFERENCE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


REFERENCE = _load_reference()
REFERENCE_CASES = {case["name"]: case for case in REFERENCE["cases"]}


def _frame(columns: dict[str, list], dtypes: dict[str, str]) -> pd.DataFrame:
    frame = pd.DataFrame(columns)
    for column, dtype in dtypes.items():
        frame[column] = frame[column].astype(dtype)
    return frame


def _case_frames(case: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    dtypes = case.get("dtypes") or {}
    reference = _frame(case["reference"], dtypes)
    current = _frame(case["current"], dtypes)
    # Guard against this file and the capture harness drifting apart: the
    # golden records the dtypes the reference engine actually saw.
    assert {c: str(t) for c, t in reference.dtypes.items()} == case["reference_dtypes"]
    assert {c: str(t) for c, t in current.dtypes.items()} == case["current_dtypes"]
    return reference, current


def _engine(case: dict[str, Any]) -> NativeDriftEngine:
    kwargs = dict(case["preset_kwargs"])
    kwargs.setdefault("drift_share", case["drift_share_threshold"])
    return NativeDriftEngine(**kwargs)


def _comparable(metrics: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    """Drop the two fields that are provenance rather than behaviour.

    ``id`` is an engine-internal fingerprint and ``config.type`` names the
    producing engine; both are expected to differ and are asserted separately.
    """

    comparable = []
    for metric in metrics:
        config = dict(metric["config"])
        assert config["type"].startswith(prefix), config["type"]
        config["type"] = config["type"][len(prefix) :]
        comparable.append(
            {"metric_name": metric["metric_name"], "config": config, "value": metric["value"]}
        )
    return comparable


def _same_number(left: Any, right: Any) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        return left == right or (math.isnan(left) and math.isnan(right))
    return left == right


# ---------------------------------------------------------------------------
# Golden equivalence against the executed Evidently 0.7.21 reference
# ---------------------------------------------------------------------------


def test_reference_capture_is_the_pinned_engine() -> None:
    assert REFERENCE["engine"] == "evidently"
    assert REFERENCE["engine_version"] == "0.7.21"
    assert REFERENCE["schema"] == "odp.drift.reference/1"
    assert len(REFERENCE["cases"]) == 65


@pytest.mark.parametrize("case_name", sorted(REFERENCE_CASES))
def test_native_engine_matches_reference_report(case_name: str) -> None:
    case = REFERENCE_CASES[case_name]
    reference, current = _case_frames(case)

    if case["error"] is not None:
        with pytest.raises(ValueError) as excinfo:
            _engine(case).run(current, reference)
        if case_name in KNOWN_ERROR_DIVERGENCES:
            assert str(excinfo.value) != case["error"]["message"]
        else:
            assert type(excinfo.value).__name__ != "ZeroDivisionError"
            assert str(excinfo.value) == case["error"]["message"]
        return

    evaluation = _engine(case).run(current, reference)
    report = evaluation.dict()

    got = _comparable(report["metrics"], METRIC_TYPE_PREFIX)
    want = _comparable(case["report"]["metrics"], REFERENCE_METRIC_TYPE_PREFIX)
    assert [metric["metric_name"] for metric in got] == [
        metric["metric_name"] for metric in want
    ]
    for got_metric, want_metric in zip(got, want, strict=True):
        assert got_metric["config"] == want_metric["config"], got_metric["metric_name"]
        if isinstance(want_metric["value"], dict):
            assert got_metric["value"] == want_metric["value"], got_metric["metric_name"]
        else:
            assert _same_number(got_metric["value"], want_metric["value"]), (
                got_metric["metric_name"]
            )

    derived = case["derived"]
    assert evaluation.number_of_drifted_columns == derived["drifted_columns"]
    assert evaluation.share_of_drifted_columns == derived["drift_share"]
    assert evaluation.dataset_drift == derived["drift_detected"]

    expected_names = KNOWN_DRIFTED_NAME_DIVERGENCES.get(case_name)
    if expected_names is None:
        assert list(evaluation.drifted_column_names) == derived["drifted_column_names"]
    else:
        reference_names, native_names = expected_names
        assert derived["drifted_column_names"] == reference_names
        assert list(evaluation.drifted_column_names) == native_names


@pytest.mark.parametrize("case_name", sorted(KNOWN_DRIFTED_NAME_DIVERGENCES))
def test_known_name_divergences_agree_with_the_drifted_column_count(case_name: str) -> None:
    """Where the released parser and the native engine disagree, the native
    answer is the one the drifted-column count corroborates."""

    case = REFERENCE_CASES[case_name]
    reference, current = _case_frames(case)
    evaluation = _engine(case).run(current, reference)

    reference_names, native_names = KNOWN_DRIFTED_NAME_DIVERGENCES[case_name]
    assert list(evaluation.drifted_column_names) == native_names
    assert len(native_names) == evaluation.number_of_drifted_columns
    assert len(reference_names) != evaluation.number_of_drifted_columns

    # The parser is what disagrees, not the report: run it over the native
    # payload and it reproduces the reference's wrong answer verbatim.
    assert list(_drifted_column_names(evaluation.dict())) == reference_names


def test_dtype_mismatch_raises_a_diagnosable_error_instead_of_dividing_by_zero() -> None:
    case = REFERENCE_CASES["dtype_mismatch_between_frames"]
    assert case["error"]["type"] == "ZeroDivisionError"
    reference, current = _case_frames(case)

    with pytest.raises(NativeDriftError, match="no drift-eligible columns"):
        _engine(case).run(current, reference)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_report_declares_the_native_engine_and_never_claims_evidently() -> None:
    reference = pd.DataFrame({"demand": [float(v) for v in range(100)]})
    report = evaluate_drift(reference=reference, current=reference).dict()

    assert report["engine"] == "native_drift"
    assert report["engine_version"] == native_drift.ENGINE_VERSION
    serialized = json.dumps(report).lower()
    assert "evidently" not in serialized
    assert "nltk" not in serialized
    for metric in report["metrics"]:
        assert metric["config"]["type"].startswith("native_drift:metric_v2:")


def test_report_json_round_trips_and_carries_stable_metric_ids() -> None:
    reference = pd.DataFrame({"demand": [float(v) for v in range(100)]})
    first = evaluate_drift(reference=reference, current=reference)
    second = evaluate_drift(reference=reference, current=reference)

    assert json.loads(first.json()) == first.dict()
    assert [m["id"] for m in first.dict()["metrics"]] == [
        m["id"] for m in second.dict()["metrics"]
    ]
    assert len({m["id"] for m in first.dict()["metrics"]}) == len(first.dict()["metrics"])


def test_native_module_does_not_import_evidently_or_nltk() -> None:
    tree = ast.parse(Path(native_drift.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            callee = node.func
            name = getattr(callee, "attr", None) or getattr(callee, "id", None)
            assert name not in {"import_module", "__import__"}, (
                "the native core must not resolve an engine at runtime"
            )
    assert "evidently" not in imported
    assert "nltk" not in imported


def test_importing_the_native_module_loads_neither_engine() -> None:
    code = (
        "import sys;"
        "import modules.learninghub.infrastructure.native_drift as m;"
        "print(int('evidently' in sys.modules), int('nltk' in sys.modules))"
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("0 0"), completed.stdout


# ---------------------------------------------------------------------------
# Column typing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        (pd.Series([1.0, 2.0, 3.0]), NativeColumnType.NUMERICAL),
        (pd.Series(list(range(10)) * 2), NativeColumnType.CATEGORICAL),
        (pd.Series(list(range(11)) * 2), NativeColumnType.NUMERICAL),
        (pd.Series(["a", "b"] * 25), NativeColumnType.CATEGORICAL),
        (pd.Series([f"n{i}" for i in range(50)] * 2), NativeColumnType.CATEGORICAL),
        (pd.Series([f"n{i}" for i in range(51)] + [f"n{i}" for i in range(49)]),
         NativeColumnType.TEXT),
        (pd.Series([True, False]), NativeColumnType.CATEGORICAL),
        (pd.Series(pd.to_datetime(["2026-01-01", "2026-01-02"])), NativeColumnType.DATETIME),
        (pd.Series([1, "a", 2, "b"], dtype=object), NativeColumnType.UNKNOWN),
        (pd.Series([[1], [2]], dtype=object), NativeColumnType.LIST),
        (pd.Series([None, None], dtype=object), NativeColumnType.UNKNOWN),
    ],
)
def test_infer_column_type(series: pd.Series, expected: NativeColumnType) -> None:
    assert infer_column_type(series) is expected


def test_integer_cardinality_limit_is_the_boundary_not_a_range() -> None:
    assert infer_column_type(pd.Series(list(range(10)) * 2)) is NativeColumnType.CATEGORICAL
    assert infer_column_type(pd.Series(list(range(11)) * 2)) is NativeColumnType.NUMERICAL


def test_text_ratio_is_strictly_greater_than_half() -> None:
    exactly_half = pd.Series([f"n{i}" for i in range(50)] * 2)
    assert exactly_half.nunique() == exactly_half.count() * 0.5
    assert infer_column_type(exactly_half) is NativeColumnType.CATEGORICAL


# ---------------------------------------------------------------------------
# Method dispatch
# ---------------------------------------------------------------------------


def _series(values) -> pd.Series:
    return pd.Series(list(values))


@pytest.mark.parametrize(
    ("rows", "combined_unique", "column_type", "expected"),
    [
        (100, 2, NativeColumnType.NUMERICAL, Z_STAT_TEST),
        (100, 3, NativeColumnType.NUMERICAL, CHI_SQUARE_STAT_TEST),
        (100, 5, NativeColumnType.NUMERICAL, CHI_SQUARE_STAT_TEST),
        (100, 6, NativeColumnType.NUMERICAL, KS_STAT_TEST),
        (1000, 6, NativeColumnType.NUMERICAL, KS_STAT_TEST),
        (1001, 2, NativeColumnType.NUMERICAL, JENSEN_SHANNON_STAT_TEST),
        (1001, 5, NativeColumnType.NUMERICAL, JENSEN_SHANNON_STAT_TEST),
        (1001, 6, NativeColumnType.NUMERICAL, WASSERSTEIN_STAT_TEST),
        (100, 2, NativeColumnType.CATEGORICAL, Z_STAT_TEST),
        (100, 3, NativeColumnType.CATEGORICAL, CHI_SQUARE_STAT_TEST),
        (100, 6, NativeColumnType.CATEGORICAL, CHI_SQUARE_STAT_TEST),
        (1001, 2, NativeColumnType.CATEGORICAL, JENSEN_SHANNON_STAT_TEST),
        (1001, 6, NativeColumnType.CATEGORICAL, JENSEN_SHANNON_STAT_TEST),
    ],
)
def test_default_stat_test_dispatch(
    rows: int, combined_unique: int, column_type: NativeColumnType, expected
) -> None:
    values = [float(index % combined_unique) for index in range(rows)]
    series = _series(values)
    assert pd.concat([series, series]).nunique() == combined_unique
    assert default_stat_test(series, series, column_type) is expected


@pytest.mark.parametrize(
    ("rows", "expected"),
    [(1000, PERCENTILE_TEXT_STAT_TEST), (1001, ABSOLUTE_TEXT_STAT_TEST)],
)
def test_default_stat_test_dispatch_for_text(rows: int, expected) -> None:
    series = _series(f"note {index}" for index in range(rows))
    assert default_stat_test(series, series, NativeColumnType.TEXT) is expected


def test_dispatch_boundary_is_taken_on_the_cleaned_reference_length() -> None:
    """1001 raw reference rows still take the small-sample branch once a null
    is stripped, exactly as the reference engine does."""

    reference = _series([float(v) for v in range(1000)] + [float("nan")])
    current = _series(float(v) for v in range(1001))
    assert len(reference) == 1001
    assert default_stat_test(
        native_drift.clean_column(reference),
        native_drift.clean_column(current),
        NativeColumnType.NUMERICAL,
    ) is KS_STAT_TEST


def test_dispatch_rejects_a_column_type_that_has_no_test() -> None:
    series = _series([1.0, 2.0, 3.0])
    with pytest.raises(NativeDriftError, match="Unexpected column type"):
        default_stat_test(series, series, NativeColumnType.DATETIME)


# ---------------------------------------------------------------------------
# Threshold direction at exact equality
# ---------------------------------------------------------------------------


def test_kolmogorov_smirnov_drifts_when_the_p_value_equals_the_threshold() -> None:
    reference = _series(float(v) for v in range(100))
    current = _series(float(v) for v in range(100))
    p_value, drifted = native_drift.kolmogorov_smirnov(
        reference, current, NativeColumnType.NUMERICAL, 1.0
    )
    assert p_value == 1.0
    assert drifted is True


@pytest.mark.parametrize("stat_test", [CHI_SQUARE_STAT_TEST, Z_STAT_TEST])
def test_p_value_tests_other_than_ks_are_strict_at_the_threshold(stat_test) -> None:
    reference = _series(["a", "b", "c"] * 33)
    current = _series(["a", "b", "c"] * 33)
    score, drifted = stat_test.func(reference, current, NativeColumnType.CATEGORICAL, 1.0)
    assert score == 1.0
    assert drifted is False


@pytest.mark.parametrize(
    "stat_test", [WASSERSTEIN_STAT_TEST, JENSEN_SHANNON_STAT_TEST]
)
def test_distance_tests_drift_when_the_distance_equals_the_threshold(stat_test) -> None:
    reference = _series(float(v) for v in range(100))
    current = _series(float(v) for v in range(100))
    score, drifted = stat_test.func(reference, current, NativeColumnType.NUMERICAL, 0.0)
    assert score == 0.0
    assert drifted is True


def test_absolute_text_content_drift_is_strict_at_the_threshold() -> None:
    reference = _series(f"reference note number {index} about rent" for index in range(60))
    current = _series(f"reference note number {index} about rent" for index in range(60))
    score, drifted = ABSOLUTE_TEXT_STAT_TEST.func(
        reference, current, NativeColumnType.TEXT, 0.0
    )
    assert score > 0.0
    assert drifted is True
    score_at, drifted_at = ABSOLUTE_TEXT_STAT_TEST.func(
        reference, current, NativeColumnType.TEXT, score
    )
    assert score_at == score
    assert drifted_at is False


def test_declared_comparison_directions_match_the_implementations() -> None:
    assert KS_STAT_TEST.comparison == "p_value<=threshold"
    assert CHI_SQUARE_STAT_TEST.comparison == "p_value<threshold"
    assert Z_STAT_TEST.comparison == "p_value<threshold"
    assert WASSERSTEIN_STAT_TEST.comparison == "distance>=threshold"
    assert JENSEN_SHANNON_STAT_TEST.comparison == "distance>=threshold"
    assert ABSOLUTE_TEXT_STAT_TEST.comparison == "distance>threshold"
    assert PERCENTILE_TEXT_STAT_TEST.comparison == "bootstrap"


# ---------------------------------------------------------------------------
# Individual statistics
# ---------------------------------------------------------------------------


def test_wasserstein_normaliser_is_floored_for_a_constant_reference() -> None:
    reference = _series([5.0] * 100)
    current = _series([5.001] * 100)
    score, _ = native_drift.wasserstein_distance_normed(
        reference, current, NativeColumnType.NUMERICAL, 0.1
    )
    assert score == pytest.approx(0.001 / 0.001, rel=1e-9)


def test_chi_square_rescales_reference_counts_to_the_current_sample_size() -> None:
    reference = _series(["a", "b"] * 50)
    current = _series(["a", "b"] * 25)
    score, drifted = native_drift.chi_square(
        reference, current, NativeColumnType.CATEGORICAL, 0.05
    )
    assert score == 1.0
    assert drifted is False


def test_chi_square_drives_a_new_category_to_zero() -> None:
    reference = _series(["north", "south"] * 50)
    current = _series((["north", "south", "west"] * 34)[:100])
    score, drifted = native_drift.chi_square(
        reference, current, NativeColumnType.CATEGORICAL, 0.05
    )
    assert score == 0.0
    assert drifted is True


def test_z_test_short_circuits_for_two_identical_constant_samples() -> None:
    reference = _series([5.0] * 100)
    score, drifted = native_drift.z_test(
        reference, reference, NativeColumnType.NUMERICAL, 0.05
    )
    assert score == 1
    assert drifted is False


def test_jensen_shannon_bins_high_cardinality_numerical_columns() -> None:
    reference = _series(float(v) for v in range(100))
    reference_percents, current_percents = native_drift.binned_distributions(
        reference, reference, NativeColumnType.NUMERICAL
    )
    assert len(reference_percents) < reference.nunique()
    assert reference_percents.tolist() == current_percents.tolist()


def test_jensen_shannon_leaves_empty_buckets_at_zero() -> None:
    reference = _series(["a"] * 100)
    current = _series(["b"] * 100)
    reference_percents, current_percents = native_drift.binned_distributions(
        reference, current, NativeColumnType.CATEGORICAL
    )
    assert sorted(reference_percents.tolist()) == [0.0, 1.0]
    assert sorted(current_percents.tolist()) == [0.0, 1.0]


def test_text_drift_separates_disjoint_vocabularies() -> None:
    # Whether any corpus package is loaded is checked in a clean interpreter by
    # test_importing_the_native_module_loads_neither_engine; sys.modules is
    # process-global and other tests in this run legitimately import Evidently.
    reference = _series(f"reference note number {index} about rent" for index in range(60))
    current = _series(f"unrelated weather headline {index} in taipei" for index in range(60))
    score, drifted = native_drift.text_domain_classifier_score(
        reference, current, bootstrap=False, threshold=0.55
    )
    assert score > 0.55
    assert drifted is True


# ---------------------------------------------------------------------------
# Cleaning and the two evaluation passes
# ---------------------------------------------------------------------------


def test_clean_column_strips_infinities_and_nulls() -> None:
    column = pd.Series([1.0, float("inf"), -float("inf"), float("nan"), 2.0])
    assert native_drift.clean_column(column).tolist() == [1.0, 2.0]


def test_counted_pass_leaves_an_inf_only_column_uncleaned() -> None:
    """An ``inf`` without a ``NaN`` reaches the counted pass untouched, so the
    two passes can legitimately disagree about one column."""

    case = REFERENCE_CASES["inf_reference_shifted_divergence"]
    reference, current = _case_frames(case)
    evaluation = NativeDriftEngine(drift_share=0.5).run(current, reference)

    assert evaluation.number_of_drifted_columns == 0
    assert evaluation.drifted_column_names == ("value",)
    assert evaluation.column("value").drift_detected is True
    assert evaluation.counted_columns[0].drift_detected is False
    assert math.isnan(evaluation.counted_columns[0].drift_score)


def test_share_denominator_excludes_untyped_columns() -> None:
    reference = pd.DataFrame(
        {
            "mixed": [None] + [1, "a"] * 49 + [None],
            "stamp": pd.to_datetime([f"2026-01-{(i % 28) + 1:02d}" for i in range(100)]),
            "demand": [float(v) for v in range(100)],
        }
    )
    current = reference.assign(demand=[float(v) for v in range(1000, 1100)])
    evaluation = NativeDriftEngine(drift_share=0.5).run(current, reference)

    assert evaluation.number_of_columns == 1
    assert evaluation.number_of_drifted_columns == 1
    assert evaluation.share_of_drifted_columns == 1.0
    assert [result.column_name for result in evaluation.value_drift] == ["demand"]


def test_dataset_drift_triggers_when_the_share_equals_the_threshold() -> None:
    reference = pd.DataFrame(
        {"a": [float(v) for v in range(100)], "b": [float(v) for v in range(100)]}
    )
    current = reference.assign(b=[float(v) for v in range(1000, 1100)])

    assert evaluate_drift(
        reference=reference, current=current, drift_share_threshold=0.5
    ).dataset_drift is True
    assert evaluate_drift(
        reference=reference, current=current, drift_share_threshold=0.6
    ).dataset_drift is False


# ---------------------------------------------------------------------------
# Counter-examples
# ---------------------------------------------------------------------------


def test_unknown_method_name_is_rejected() -> None:
    reference = pd.DataFrame({"demand": [float(v) for v in range(100)]})
    with pytest.raises(StatTestNotFoundError, match="psi"):
        NativeDriftEngine(method="psi").run(reference, reference)


def test_method_rejects_an_incompatible_column_type() -> None:
    reference = pd.DataFrame({"region": ["north", "south"] * 50})
    with pytest.raises(StatTestInvalidColumnTypeError, match="'cat'"):
        NativeDriftEngine(method="ks").run(reference, reference)


def test_missing_column_in_the_reference_frame_is_rejected() -> None:
    reference = pd.DataFrame({"demand": [float(v) for v in range(100)]})
    current = pd.DataFrame({"rent": [float(v) for v in range(100)]})
    with pytest.raises(NativeDriftError, match="Cannot find column 'rent' in reference"):
        NativeDriftEngine().run(current, reference)


def test_numerical_column_with_non_numeric_reference_values_is_rejected() -> None:
    reference = pd.DataFrame({"demand": ["a", "b"] * 50})
    current = pd.DataFrame({"demand": [float(v) for v in range(100)]})
    with pytest.raises(NativeDriftError, match="no drift-eligible columns"):
        NativeDriftEngine().run(current, reference)


def test_non_dataframe_input_is_rejected() -> None:
    reference = pd.DataFrame({"demand": [float(v) for v in range(100)]})
    with pytest.raises(NativeDriftError, match="pandas DataFrames"):
        NativeDriftEngine().run([{"demand": 1.0}], reference)


def test_every_registered_test_declares_the_column_types_it_accepts() -> None:
    assert set(STAT_TESTS) == {
        "ks",
        "chisquare",
        "z",
        "wasserstein",
        "jensenshannon",
        "perc_text_content_drift",
        "abs_text_content_drift",
    }
    for stat_test in STAT_TESTS.values():
        assert stat_test.allowed_column_types
        assert 0 < stat_test.default_threshold <= 1


# ---------------------------------------------------------------------------
# Hand-over surface for the cutover
# ---------------------------------------------------------------------------


def test_evaluation_exposes_every_field_the_production_consumers_read() -> None:
    reference = pd.DataFrame({"prediction": [float(v) for v in range(1, 101)]})
    current = pd.DataFrame({"prediction": [float(v) for v in range(1001, 1101)]})
    evaluation = NativeDriftEngine(drift_share=0.5).run(current, reference)

    assert evaluation.drifted_column_names == ("prediction",)
    assert evaluation.number_of_drifted_columns == 1
    assert evaluation.share_of_drifted_columns == 1.0
    assert evaluation.dataset_drift is True
    assert evaluation.number_of_columns == 1

    summary = evaluation.to_dict()
    assert summary["engine"] == "native_drift"
    assert summary["value_drift"][0]["stattest_name"] == "ks"
    assert summary["value_drift"][0]["stattest_display_name"] == "K-S p_value"
    assert summary["value_drift"][0]["stattest_threshold"] == 0.05
    assert summary["value_drift"][0]["column_type"] == "num"


def test_engine_argument_order_matches_the_engine_it_replaces() -> None:
    """``run(current, reference)`` -- the same order as the released call."""

    reference = pd.DataFrame({"demand": [float(v) for v in range(100)]})
    current = pd.DataFrame({"demand": [float(v) for v in range(1000, 1100)]})

    positional = NativeDriftEngine(drift_share=0.5).run(current, reference)
    keyword = evaluate_drift(reference=reference, current=current, drift_share_threshold=0.5)
    assert positional.dict() == keyword.dict()
