from pathlib import Path

from scripts.chaos.assisted_listing_intake.run import run_drills


def test_fault_matrix_and_recovery_targets_are_measured(tmp_path: Path) -> None:
    report = run_drills(tmp_path / "chaos.sqlite3")

    assert {event["scenario"] for event in report["events"]} == {
        "provider_latency",
        "duplicate_delivery",
        "worker_loss",
        "sql_failover",
        "gcs_inconsistency",
        "queue_backlog",
        "retry_budget_dlq_recovery",
    }
    assert report["measured_rpo_seconds"] <= report["rpo_target_seconds"]
    assert report["measured_rto_seconds"] <= report["rto_target_seconds"]
    assert report["missed_targets"] == []
    assert report["passed"] is True
