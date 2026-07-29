#!/usr/bin/env python3
"""Tests for the criterion-5 probe's `model_quality_probe` stage.

The stage's first live execution is the finisher's post-activation run, hours
after any worker is awake to watch it. That is exactly the situation in which
untested code is worst: nobody sees the traceback, and the acceptance package
loses its criterion-5 receipt. So the stage is exercised here against the
repository's own forecastops fixtures, which build real `PreparedRow`s through
the real `prepare_model_rows`, and against the real LightGBM trainer.

Four properties, each of which would be a silent defect in the finisher:

1. The stage runs and returns a PASS/FAIL verdict on real prepared rows,
   invoking the real `_temporal_validation` through a namespace carrying only
   `regression_trainer`.
2. No segment value reaches the receipt. `_segment_validation` embeds the
   segment value -- a store id -- in its per-segment records and in its failure
   strings, and this evidence directory publishes counts only.
3. A raising `_temporal_validation` degrades to `status: ERROR` rather than
   propagating, so a probe that has already spent forty minutes loading rows
   still writes its data-gate receipt.
4. A `FAIL` from the stage does not move `data_gates_passed` or
   `blocking_gate`. This is the whole reason the stage is safe to add: the
   thresholds it scores are owned by ODP-PRODUCTION-MODEL-REGISTRY-001, not by
   this backfill.

Run:  python3 runbook/training-contract-quality-stage-test.py
Needs no database -- fixtures only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.models.contracts import MODEL_SPECS  # noqa: E402
from scripts.models.release import prepare_model_rows  # noqa: E402
from tests.integration.test_model_training_release import (  # noqa: E402
    _loaded,
    _raw_forecast_rows,
)


def _load_probe():
    """Import the probe by path. `main()` is guarded, so nothing runs."""
    path = os.path.join(HERE, "training-contract-readiness-probe.py")
    spec = importlib.util.spec_from_file_location("training_contract_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage_runs_and_redacts(probe, spec, prepared) -> None:
    out = probe._safe_model_quality_probe(spec, prepared)
    assert out["status"] in ("PASS", "FAIL"), out
    assert out["gates_this_task"] is False
    assert out["owned_by"] == "ODP-PRODUCTION-MODEL-REGISTRY-001"
    assert out["algorithm_resolved"], out
    assert out["training_rows"] > 0 and out["holdout_rows"] > 0
    assert "elapsed_seconds" in out

    blob = json.dumps(out, default=str)
    segments = {row.segment_value for row in prepared if row.segment_value}
    leaked = sorted(value for value in segments if value in blob)
    assert not leaked, f"segment values reached the receipt: {leaked}"
    print(f"  stage: {out['status']} in {out['elapsed_seconds']}s, "
          f"{out['segment_outcomes']['segments_scored']} segments, no leak")


def test_stage_errors_are_contained(probe, spec, prepared) -> None:
    # One row cannot be temporally split; _temporal_validation raises.
    out = probe._safe_model_quality_probe(spec, prepared[:1])
    assert out["status"] == "ERROR", out
    assert "error" in out and out["error"], out
    print(f"  contained: {out['error'][:70]}")


def test_quality_failure_does_not_block_the_data_verdict(probe) -> None:
    out_path = tempfile.mktemp(suffix=".json")
    original, probe.OUT = probe.OUT, out_path
    try:
        probe._write(
            {
                "gates": [{"gate": "g", "status": "PASS", "detail": "x"}],
                "model_quality_probe": {"status": "FAIL"},
            },
            blocking=None,
        )
        verdict = json.load(open(out_path))["verdict"]
    finally:
        probe.OUT = original
    assert verdict["data_gates_passed"] is True, verdict
    assert verdict["blocking_gate"] is None, verdict
    assert verdict["model_quality_probe"].startswith("FAIL"), verdict
    print("  quality FAIL left data_gates_passed=True")


def test_blocked_run_reports_the_stage_as_unreached(probe) -> None:
    out_path = tempfile.mktemp(suffix=".json")
    original, probe.OUT = probe.OUT, out_path
    try:
        probe._write(
            {"gates": [{"gate": "eligible_rows_exist", "status": "FAIL", "detail": "x"}]},
            blocking="eligible_rows_exist",
        )
        verdict = json.load(open(out_path))["verdict"]
    finally:
        probe.OUT = original
    assert verdict["data_gates_passed"] is False, verdict
    assert "not reached" in verdict["model_quality_probe"], verdict
    print("  blocked run reported the stage as not reached")


def main() -> int:
    probe = _load_probe()
    spec = MODEL_SPECS["forecastops"]
    prepared = prepare_model_rows(spec, _loaded(_raw_forecast_rows(240)))
    print(f"prepared {len(prepared)} fixture rows for {spec.key}")

    for test in (
        lambda: test_stage_runs_and_redacts(probe, spec, prepared),
        lambda: test_stage_errors_are_contained(probe, spec, prepared),
        lambda: test_quality_failure_does_not_block_the_data_verdict(probe),
        lambda: test_blocked_run_reports_the_stage_as_unreached(probe),
    ):
        print(test.__name__ if hasattr(test, "__name__") else "test")
        test()

    print("\nall criterion-5 quality-stage tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
