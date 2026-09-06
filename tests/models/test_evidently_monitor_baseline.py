"""Executable baseline for the pinned Evidently 0.7.21 drift-monitoring stack.

Scope. This suite freezes what the *current* engine actually does. It calls the
real ``EvidentlyDriftMonitor.run`` / ``run_prediction`` against the locked
``evidently==0.7.21`` (which is what drags in the unpatched ``nltk 3.10.3``), and
compares every statistic, method, threshold and drift verdict against golden
files that were read back from a live run -- never hand-authored.

Non-scope, stated so no reader over-reads a green run:

* This is a *baseline*, not an equivalence proof. A green run says the pinned
  engine still behaves the way it behaved when the fixtures were generated. It
  says nothing about whether any replacement engine is equivalent, and it is not
  a security result: ``nltk 3.10.3`` remains present and unpatched, and this
  suite neither changes nor consults any security gate.
* The float tolerance below exists because BLAS/CPU differences are not
  bit-guaranteed for the *same* engine. It must not be reused as an accepted
  tolerance for a different engine; §6.3 of the disposition requires that
  tolerance to be derived per algorithm and approved by a reviewer.
* Everything the fixtures do not cover is recorded as a limitation in
  ``docs/evidence/completion/ODP-NLTK-MONITORING-BASELINE-001/``. Absence of a
  case here means "not measured", never "does not exist".
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "evidently_0_7_21"
CASES_DIR = FIXTURE_DIR / "cases"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Same-engine reproducibility tolerance only. See the module docstring.
FLOAT_REL_TOL = 1e-9
FLOAT_ABS_TOL = 1e-12


def _load_cases_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "evidently_0_7_21_baseline_cases", FIXTURE_DIR / "baseline_cases.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cases = _load_cases_module()
MANIFEST: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

COMPLETED_CASES = (*cases.DATA_DRIFT_CASES, *cases.PREDICTION_CASES)
FAILURE_CASES = (*cases.DATA_DRIFT_FAILURE_CASES, *cases.PREDICTION_FAILURE_CASES)


def _case_id(case: Any) -> str:
    return str(case.case_id)


def _golden(case_id: str) -> dict[str, Any]:
    return json.loads((CASES_DIR / f"{case_id}.golden.json").read_text(encoding="utf-8"))


def _compare(observed: Any, expected: Any, path: str, failures: list[str]) -> None:
    """Structural equality everywhere, tolerance only on non-boolean numbers."""

    if isinstance(expected, Mapping):
        if not isinstance(observed, Mapping):
            failures.append(f"{path}: expected a mapping, got {type(observed).__name__}")
            return
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        if missing:
            failures.append(f"{path}: missing keys {missing}")
        if extra:
            failures.append(f"{path}: unexpected keys {extra}")
        for key in sorted(set(expected) & set(observed)):
            _compare(observed[key], expected[key], f"{path}[{key!r}]", failures)
        return
    if isinstance(expected, list):
        if not isinstance(observed, list):
            failures.append(f"{path}: expected a list, got {type(observed).__name__}")
            return
        if len(observed) != len(expected):
            failures.append(f"{path}: length {len(observed)} != baseline {len(expected)}")
            return
        for index, (observed_item, expected_item) in enumerate(zip(observed, expected, strict=True)):
            _compare(observed_item, expected_item, f"{path}[{index}]", failures)
        return
    if isinstance(expected, bool) or isinstance(observed, bool):
        if observed is not expected:
            failures.append(f"{path}: {observed!r} != baseline {expected!r}")
        return
    if isinstance(expected, (int, float)) and isinstance(observed, (int, float)):
        if not cases.is_close(observed, expected, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL):
            failures.append(f"{path}: {observed!r} != baseline {expected!r}")
        return
    if observed != expected:
        failures.append(f"{path}: {observed!r} != baseline {expected!r}")


def _assert_matches_baseline(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    failures: list[str] = []
    _compare(observed, expected, "$", failures)
    if failures:
        raise AssertionError(
            "live Evidently 0.7.21 run diverged from the recorded baseline:\n  "
            + "\n  ".join(failures)
        )


# ---------------------------------------------------------------------------
# Fixture integrity and provenance
# ---------------------------------------------------------------------------


def test_manifest_pins_the_runtime_the_baseline_was_recorded_against() -> None:
    """The recorded statistics are only meaningful under the recorded versions."""

    for package, recorded in MANIFEST["packages"].items():
        assert recorded is not None, f"manifest has no version for {package}"
        assert version(package) == recorded, (
            f"{package} is {version(package)} but the baseline was recorded against "
            f"{recorded}; regenerate the baseline instead of relaxing this assertion"
        )
    # evidently 0.7.21 is the reason nltk 3.10.3 is in the dependency graph at
    # all, so both are pinned explicitly rather than left to the loop above.
    assert MANIFEST["packages"]["evidently"] == "0.7.21"
    assert MANIFEST["packages"]["nltk"] == "3.10.3"

    recorded_python = MANIFEST["environment"]["python_version"].split(".")[:2]
    assert list(sys.version_info[:2]) == [int(part) for part in recorded_python]


def test_fixture_files_match_the_hashes_recorded_in_the_manifest() -> None:
    """A hand-edited golden file must not be able to pass as a runtime read-back."""

    for case_id, receipt in MANIFEST["cases"].items():
        golden_path = CASES_DIR / f"{case_id}.golden.json"
        assert golden_path.is_file(), f"missing golden file for {case_id}"
        digest = hashlib.sha256(golden_path.read_bytes()).hexdigest()
        assert digest == receipt["golden_sha256"], f"{golden_path.name} was modified in place"

        raw_sha = receipt["raw_report_sha256"]
        raw_path = CASES_DIR / f"{case_id}.raw.json"
        if raw_sha is None:
            assert not raw_path.exists(), f"{case_id} raised, so it must have no raw report"
            continue
        assert raw_path.is_file(), f"missing raw evaluation for {case_id}"
        assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == raw_sha


def test_manifest_covers_exactly_the_declared_cases() -> None:
    assert MANIFEST["case_count"] == len(cases.ALL_CASES)
    assert set(MANIFEST["cases"]) == {case.case_id for case in cases.ALL_CASES}


@pytest.mark.parametrize("case", cases.ALL_CASES, ids=_case_id)
def test_recorded_inputs_still_describe_the_live_fixtures(case: Any) -> None:
    """Input hashes, cleaned N, dtypes and merged unique counts are re-derived."""

    receipt = MANIFEST["cases"][case.case_id]
    fresh = cases.input_receipt(case)
    for key in (
        "reference_row_count",
        "current_row_count",
        "reference_rows_sha256",
        "current_rows_sha256",
        "columns",
    ):
        assert fresh[key] == receipt[key], (
            f"{case.case_id}: fixture input {key} changed since the baseline was recorded"
        )


def test_normalization_policy_is_recorded_and_narrow() -> None:
    """Only the auto-generated snapshot id may be normalized away."""

    assert MANIFEST["normalization"] == cases.NORMALIZATION_POLICY
    assert len(cases.NORMALIZATION_POLICY["normalized"]) == 1
    assert "snapshot_id" in cases.NORMALIZATION_POLICY["normalized"][0]


# ---------------------------------------------------------------------------
# Runtime reproduction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", COMPLETED_CASES, ids=_case_id)
def test_case_reproduces_the_recorded_baseline(case: Any) -> None:
    """Replay the case against the live engine and diff the whole contract."""

    expected = _golden(case.case_id)
    assert expected["outcome"] == "completed"

    result = cases.execute(case)
    observed = cases.normalize_result(result, expects_auto_snapshot=case.expects_auto_snapshot)

    _assert_matches_baseline(observed["result"], expected["result"])
    _assert_matches_baseline(observed["metrics_index"], expected["metrics_index"])
    # to_dict() is the structure the §6.3 report-equivalence criterion pins:
    # a metrics array carrying DriftedColumnsCount and ValueDrift(column=...).
    _assert_matches_baseline(observed["to_dict"], expected["to_dict"])


@pytest.mark.parametrize("case", FAILURE_CASES, ids=_case_id)
def test_failure_case_reproduces_the_recorded_failure(case: Any) -> None:
    """Refusals are part of the contract and are compared verbatim, not relaxed."""

    expected = _golden(case.case_id)
    assert expected["outcome"] == "raised"

    with pytest.raises(Exception) as caught:  # noqa: B017 - the type is the assertion
        cases.execute(case)

    assert cases.normalize_failure(caught.value) == expected["failure"]


# ---------------------------------------------------------------------------
# The §5.1 stat-test routing table, asserted against the runtime read-back
# ---------------------------------------------------------------------------

#: Written by hand from the disposition's routing table and then checked against
#: what the pinned runtime actually reported, so a silent change of selector in
#: any future Evidently bump fails here with a readable name.
EXPECTED_ROUTING: dict[str, dict[str, str]] = {
    "numeric_ks_stable": {"demand": "K-S p_value"},
    "numeric_ks_drifted": {"demand": "K-S p_value"},
    "numeric_merged_unique_2_ztest": {"demand": "Z-test p_value"},
    "numeric_merged_unique_3_chisquare": {"demand": "chi-square p_value"},
    "numeric_merged_unique_5_chisquare": {"demand": "chi-square p_value"},
    "numeric_merged_unique_6_ks": {"demand": "K-S p_value"},
    "numeric_reference_n_1000_ks": {"demand": "K-S p_value"},
    "numeric_reference_n_1001_wasserstein": {"demand": "Wasserstein distance (normed)"},
    "numeric_reference_n_1001_jensenshannon": {"demand": "Jensen-Shannon distance"},
    "numeric_nan_cleaned_to_1000_ks": {"demand": "K-S p_value"},
    "numeric_nan_cleaned_to_1001_wasserstein": {"demand": "Wasserstein distance (normed)"},
    "numeric_infinity_cleaned_to_1001_wasserstein": {"demand": "Wasserstein distance (normed)"},
    "numeric_constant_stable_ztest": {"demand": "Z-test p_value"},
    "numeric_constant_shifted_ztest": {"demand": "Z-test p_value"},
    "categorical_merged_unique_2_ztest": {"region": "Z-test p_value"},
    "categorical_merged_unique_3_chisquare": {"region": "chi-square p_value"},
    "categorical_reference_n_1001_jensenshannon": {"region": "Jensen-Shannon distance"},
    "categorical_new_label_chisquare": {"region": "chi-square p_value"},
    "mixed_share_meets_threshold": {"demand": "K-S p_value", "rent": "K-S p_value"},
    "mixed_share_below_threshold": {"demand": "K-S p_value", "rent": "K-S p_value"},
    "numeric_gaussian_stable_ks": {"demand": "K-S p_value"},
    "numeric_gaussian_drifted_ks": {"demand": "K-S p_value"},
    "text_percentile_stable": {"note": "Percentile text content drift"},
    "text_percentile_drifted": {"note": "Percentile text content drift"},
    "text_absolute_drifted": {"note": "Absolute text content drift"},
    "prediction_numeric_stable": {"prediction": "K-S p_value"},
    "prediction_numeric_drifted": {"prediction": "K-S p_value"},
    "prediction_categorical_stable": {"prediction": "Z-test p_value"},
    "prediction_categorical_new_class_drifted": {"prediction": "chi-square p_value"},
    "prediction_mixed_output_types": {
        "prediction": "K-S p_value",
        "risk_band": "Z-test p_value",
    },
    "prediction_inferred_output_types": {
        "prediction": "K-S p_value",
        "risk_band": "Z-test p_value",
    },
    "prediction_policy_threshold_only": {
        "prediction": "K-S p_value",
        "confidence": "K-S p_value",
    },
    "prediction_requested_threshold_tightens_policy": {
        "prediction": "K-S p_value",
        "confidence": "K-S p_value",
    },
}


def test_routing_table_is_complete_for_every_completed_case() -> None:
    assert set(EXPECTED_ROUTING) == {case.case_id for case in COMPLETED_CASES}


@pytest.mark.parametrize("case", COMPLETED_CASES, ids=_case_id)
def test_recorded_stat_test_matches_the_routing_table(case: Any) -> None:
    columns = _golden(case.case_id)["metrics_index"]["columns"]
    observed = {name: facts["method"] for name, facts in columns.items()}
    assert observed == EXPECTED_ROUTING[case.case_id]


def test_cleaned_reference_size_and_not_the_raw_row_count_drives_the_branch() -> None:
    """The 1000/1001 boundary is measured on the cleaned reference column.

    ``numeric_nan_cleaned_to_1000_ks`` has 1001 raw rows yet takes the <=1000
    branch, and ``numeric_infinity_cleaned_to_1001_wasserstein`` has 1002 raw
    rows and takes the >1000 branch, which is only explicable by the cleaning
    step in ``evidently/legacy/calculations/data_drift.py``.
    """

    small = MANIFEST["cases"]["numeric_nan_cleaned_to_1000_ks"]["columns"]["demand"]
    assert small["raw_reference_n"] == 1001
    assert small["cleaned_reference_n"] == 1000
    assert _golden("numeric_nan_cleaned_to_1000_ks")["metrics_index"]["columns"]["demand"][
        "method"
    ] == "K-S p_value"

    large = MANIFEST["cases"]["numeric_infinity_cleaned_to_1001_wasserstein"]["columns"]["demand"]
    assert large["raw_reference_n"] == 1002
    assert large["cleaned_reference_n"] == 1001
    assert _golden("numeric_infinity_cleaned_to_1001_wasserstein")["metrics_index"]["columns"][
        "demand"
    ]["method"] == "Wasserstein distance (normed)"


def test_merged_unique_boundaries_recorded_by_the_manifest() -> None:
    """§6.3 asks for the 2/3/5/6 merged-unique boundary values explicitly."""

    boundaries = {
        "numeric_merged_unique_2_ztest": 2,
        "numeric_merged_unique_3_chisquare": 3,
        "numeric_merged_unique_5_chisquare": 5,
        "numeric_merged_unique_6_ks": 6,
    }
    for case_id, expected_unique in boundaries.items():
        assert MANIFEST["cases"][case_id]["columns"]["demand"]["merged_unique"] == expected_unique


# ---------------------------------------------------------------------------
# Verdict semantics
# ---------------------------------------------------------------------------


def test_drift_share_threshold_governs_the_dataset_verdict_only() -> None:
    """Same inputs, two thresholds: the column list is unchanged, the flag flips."""

    meets = _golden("mixed_share_meets_threshold")["result"]
    below = _golden("mixed_share_below_threshold")["result"]

    assert meets["drift_share"] == below["drift_share"] == 0.5
    assert meets["drifted_columns"] == below["drifted_columns"] == 1
    assert meets["drifted_column_names"] == below["drifted_column_names"] == ["demand"]
    assert meets["drift_detected"] is True
    assert below["drift_detected"] is False


@pytest.mark.parametrize("case", COMPLETED_CASES, ids=_case_id)
def test_first_party_column_list_agrees_with_the_engine_drift_count(case: Any) -> None:
    """``_drifted_column_names`` re-derives per-column verdicts from the report.

    Evidently reports the authoritative count in ``DriftedColumnsCount``; the
    first-party parser re-implements the per-column rule from the metric name.
    Every recorded case agrees, and this asserts that they keep agreeing. It is
    not a proof that the two rules coincide for all inputs -- see the text-branch
    limitation recorded in the completion evidence.
    """

    golden = _golden(case.case_id)
    assert len(golden["result"]["drifted_column_names"]) == golden["result"]["drifted_columns"]
    assert golden["metrics_index"]["drifted_columns_count"]["count"] == float(
        golden["result"]["drifted_columns"]
    )


def test_prediction_threshold_resolution_prefers_the_stricter_value() -> None:
    """A caller may tighten the governed threshold; weakening it is a hard error."""

    governed_only = _golden("prediction_policy_threshold_only")["result"]
    tightened = _golden("prediction_requested_threshold_tightens_policy")["result"]

    assert governed_only["drift_share"] == tightened["drift_share"] == 0.5
    assert governed_only["drifted_column_names"] == tightened["drifted_column_names"] == [
        "prediction"
    ]
    # 0.5 < governed 0.8 -> no dataset-level alert
    assert governed_only["drift_detected"] is False
    # 0.5 >= requested 0.4 -> alert, because the caller tightened the threshold
    assert tightened["drift_detected"] is True
    assert (
        governed_only["decision_policy_version_id"]
        == tightened["decision_policy_version_id"]
        == cases.baseline_policy(0.8).policy_version_id
    )

    weakened = _golden("failure_prediction_requested_threshold_weakens_policy")
    assert weakened["failure"]["exception_type"] == "ValueError"
    assert "weaken" in weakened["failure"]["exception_message"]


def test_prediction_results_carry_the_governed_metadata() -> None:
    result = _golden("prediction_numeric_drifted")["result"]

    assert result["model_name"] == "revenue-model"
    assert result["model_version"] == "v1"
    assert result["cohort_key"] == "region:north"
    assert result["reference_snapshot_id"] == "snapshot-reference"
    assert result["current_snapshot_id"] == "snapshot-current"
    assert result["snapshot_id"] == "snapshot-current"
    assert result["prediction_columns"] == ["prediction"]
    assert result["prediction_output_types"] == {"prediction": "numeric"}
    # entity_id is present on every fixture row but outside prediction_columns,
    # so it must not appear as a monitored column.
    assert "entity_id" not in _golden("prediction_numeric_drifted")["metrics_index"]["columns"]


def test_output_types_are_inferred_when_the_caller_omits_them() -> None:
    inferred = _golden("prediction_inferred_output_types")["result"]

    assert inferred["prediction_output_types"] == {
        "prediction": "numeric",
        "risk_band": "categorical",
    }


# ---------------------------------------------------------------------------
# Text branch: reachable from the first-party wrapper, with recorded limits
# ---------------------------------------------------------------------------


def test_text_stat_tests_are_reachable_through_the_first_party_wrapper() -> None:
    """The Text branch is not hypothetical for this codebase.

    ``EvidentlyDriftMonitor.run`` passes a plain ``DataFrame`` with no
    ``DataDefinition``, so Evidently infers the column type. A high-cardinality
    free-text column is inferred as ``ColumnType.Text`` and both text stat-tests
    are selected -- which means a caller passing free text reaches this branch
    without opting in.
    """

    small = _golden("text_percentile_stable")["metrics_index"]["columns"]["note"]
    large = _golden("text_absolute_drifted")["metrics_index"]["columns"]["note"]

    assert small["method"] == "Percentile text content drift"
    assert small["threshold"] == 0.95
    assert large["method"] == "Absolute text content drift"
    assert large["threshold"] == 0.55

    assert _golden("text_percentile_stable")["result"]["drift_detected"] is False
    assert _golden("text_percentile_drifted")["result"]["drift_detected"] is True
    assert _golden("text_absolute_drifted")["result"]["drift_detected"] is True


def test_text_threshold_is_a_bootstrap_parameter_not_a_distance_cut() -> None:
    """Record a real divergence risk instead of asserting equivalence.

    ``_drift_metric_detected`` treats any method whose name lacks ``p_value`` as
    a distance test and flags drift when ``value >= threshold``. For the text
    branch the ``threshold`` in the metric name is the bootstrap parameter
    (``p_value=1-threshold``), not a distance cut, so the first-party rule and
    Evidently's own rule are not the same rule. Every text fixture recorded here
    happens to agree; that agreement is measured, not guaranteed.
    """

    stable = _golden("text_percentile_stable")
    drifted = _golden("text_percentile_drifted")

    assert stable["metrics_index"]["columns"]["note"]["value"] < 0.95
    assert stable["result"]["drifted_column_names"] == []
    assert stable["metrics_index"]["drifted_columns_count"]["count"] == 0.0

    assert drifted["metrics_index"]["columns"]["note"]["value"] >= 0.95
    assert drifted["result"]["drifted_column_names"] == ["note"]
    assert drifted["metrics_index"]["drifted_columns_count"]["count"] == 1.0


# ---------------------------------------------------------------------------
# The normalizer itself is under test
# ---------------------------------------------------------------------------


def _synthetic_result(*, snapshot_id: str, p_value: float) -> Any:
    from modules.learninghub.infrastructure.evidently_monitor import EvidentlyDriftResult

    report = {
        "metrics": [
            {
                "id": "0" * 32,
                "metric_name": "DriftedColumnsCount(drift_share=0.5)",
                "config": {"type": "evidently:metric_v2:DriftedColumnsCount", "drift_share": 0.5},
                "value": {"count": 0.0, "share": 0.0},
            },
            {
                "id": "1" * 32,
                "metric_name": "ValueDrift(column=demand,method=K-S p_value,threshold=0.05)",
                "config": {
                    "type": "evidently:metric_v2:ValueDrift",
                    "column": "demand",
                    "method": "K-S p_value",
                    "threshold": 0.05,
                },
                "value": p_value,
            },
        ],
        "tests": [],
    }
    return EvidentlyDriftResult(
        snapshot_id=snapshot_id,
        drift_detected=False,
        drifted_columns=0,
        drift_share=0.0,
        report_json=json.dumps(report),
    )


def test_normalizer_replaces_only_an_auto_generated_snapshot_id() -> None:
    auto = _synthetic_result(
        snapshot_id="evidently-0f1e2d3c-4b5a-4968-8776-65544332211f", p_value=1.0
    )
    normalized = cases.normalize_result(auto, expects_auto_snapshot=True)
    assert normalized["result"]["snapshot_id"] == cases.AUTO_SNAPSHOT_PLACEHOLDER
    assert normalized["to_dict"]["snapshot_id"] == cases.AUTO_SNAPSHOT_PLACEHOLDER

    supplied = _synthetic_result(snapshot_id="caller-supplied", p_value=1.0)
    preserved = cases.normalize_result(supplied, expects_auto_snapshot=False)
    assert preserved["result"]["snapshot_id"] == "caller-supplied"


def test_normalizer_refuses_a_snapshot_id_of_the_wrong_provenance() -> None:
    supplied = _synthetic_result(snapshot_id="caller-supplied", p_value=1.0)
    with pytest.raises(AssertionError, match="auto-generated evidently-<uuid4>"):
        cases.normalize_result(supplied, expects_auto_snapshot=True)

    auto = _synthetic_result(
        snapshot_id="evidently-0f1e2d3c-4b5a-4968-8776-65544332211f", p_value=1.0
    )
    with pytest.raises(AssertionError, match="caller supplied a snapshot id"):
        cases.normalize_result(auto, expects_auto_snapshot=False)


def test_normalizer_never_smooths_a_statistic_away() -> None:
    """A changed p-value must survive normalization and fail the comparison."""

    baseline = cases.normalize_result(
        _synthetic_result(snapshot_id="fixed", p_value=1.0), expects_auto_snapshot=False
    )
    perturbed = cases.normalize_result(
        _synthetic_result(snapshot_id="fixed", p_value=0.9999), expects_auto_snapshot=False
    )

    assert perturbed["metrics_index"]["columns"]["demand"]["value"] == 0.9999
    assert perturbed != baseline
    with pytest.raises(AssertionError, match="0.9999"):
        _assert_matches_baseline(perturbed["metrics_index"], baseline["metrics_index"])


# ---------------------------------------------------------------------------
# Runtime footprint of the pinned engine
# ---------------------------------------------------------------------------

_NLTK_IMPORT_PROBE = """
import json, sys
from modules.learninghub.infrastructure import EvidentlyDriftMonitor

before_run = sorted(m for m in sys.modules if m == "nltk" or m.startswith("nltk."))
EvidentlyDriftMonitor().run(
    reference_rows=[{"demand": float(v)} for v in range(1, 51)],
    current_rows=[{"demand": float(v)} for v in range(1, 51)],
    drift_share_threshold=0.5,
    snapshot_id="import-probe",
)
loaded = sorted(m for m in sys.modules if m == "nltk" or m.startswith("nltk."))
print("PROBE" + json.dumps({
    "before_run": before_run,
    "loaded_count": len(loaded),
    "vulnerable_api_modules": {
        name: name in sys.modules
        for name in (
            "nltk.parse.transitionparser",
            "nltk.tag.perceptron",
            "nltk.classify.maxent",
        )
    },
}))
"""


def test_a_plain_numeric_drift_run_loads_nltk_into_the_process() -> None:
    """Measured import reachability, in a fresh interpreter.

    The disposition (§3.3) requires reachability to be decided by a probe
    against the pinned runtime rather than inferred from the absence of a
    first-party ``import nltk``. This runs the most ordinary call the platform
    makes -- a numeric ``DataDriftPreset`` with no text column anywhere -- and
    records what ends up in ``sys.modules``.

    What this asserts: the modules that define the advisory's affected APIs are
    imported into the production process by an ordinary monitoring call. What it
    does **not** assert: that any of those APIs is invoked, that the flaw is
    exploitable, or anything at all about remediation status. Module import is
    reachability evidence, not an exploitability finding.
    """

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", _NLTK_IMPORT_PROBE],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    probe_lines = [line for line in completed.stdout.splitlines() if line.startswith("PROBE")]
    assert probe_lines, completed.stdout[-2000:]
    probe = json.loads(probe_lines[-1][len("PROBE") :])

    # Importing the first-party monitor alone does not pull nltk in; the
    # Evidently import inside run() does.
    assert probe["before_run"] == []
    assert probe["loaded_count"] > 0
    assert probe["vulnerable_api_modules"] == {
        "nltk.parse.transitionparser": True,
        "nltk.tag.perceptron": True,
        "nltk.classify.maxent": True,
    }
