from pathlib import Path

from scripts.load.assisted_listing_intake.runtime import run_capacity


def test_approved_capacity_and_slo_are_measured(tmp_path: Path) -> None:
    report = run_capacity(tmp_path / "capacity.sqlite3", volume=120, concurrency=20)

    assert report["volume"] == 120
    assert report["batch_size"] <= 1000
    assert report["concurrency"] <= 100
    assert report["throughput_rows_per_second"] > 0
    assert report["availability"] >= 0.9995
    assert report["passed"] is True


def test_error_budget_is_derived_from_samples(tmp_path: Path) -> None:
    report = run_capacity(tmp_path / "budget.sqlite3", volume=40, concurrency=5)

    assert report["error_budget"]["receipt_over_target_fraction"] <= 0.01
    assert report["error_budget"]["parse_over_target_fraction"] <= 0.02

