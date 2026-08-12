from pathlib import Path

import pytest

from scripts.load.assisted_listing_intake.runtime import Sample, run_capacity, summarize

pytestmark = pytest.mark.performance


def test_durable_capacity_measurement_records_queue_invariants(tmp_path: Path) -> None:
    report = run_capacity(tmp_path / "capacity.sqlite3", volume=20, concurrency=5)

    assert report["volume"] == 20
    assert report["batch_size"] <= 1000
    assert report["concurrency"] == 5
    assert report["observed_peak_submitters"] == 5
    assert report["observed_peak_workers"] == 5
    assert report["throughput_rows_per_second"] > 0
    assert report["availability"] >= 0.9995
    assert {item["target"] for item in report["not_executed_targets"]} == {
        "human_review_completion_sla",
        "managed_service_capacity",
    }
    assert report["production_ready"] is False
    receipt = next(sli for sli in report["slis"] if sli["name"] == "url_submission_durable_receipt")
    assert report["error_budget"]["receipt_over_target_fraction"] == receipt["over_target_fraction"]
    assert report["passed"] is (not report["missed_targets"])


def test_error_budget_is_derived_from_samples() -> None:
    summary = summarize(
        "durable_receipt",
        [
            Sample("durable_receipt", 0.1, 0.1),
            Sample("durable_receipt", 0.6, 0.6),
            Sample("durable_receipt", 1.6, 1.6),
        ],
        p95_target=0.5,
        p99_target=1.5,
    )

    assert summary["over_target_fraction"] == 2 / 3
    assert summary["passed"] is False
