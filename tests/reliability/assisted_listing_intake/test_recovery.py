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
    assert set(report["missed_production_targets"]) == {
        "managed_provider_latency_injection",
        "cloud_sql_regional_failover",
        "gcs_generation_and_checksum_reconciliation",
        "cloud_tasks_pubsub_backlog_and_dlq",
        "production_restore_drill",
    }
    retry_event = next(
        event for event in report["events"]
        if event["scenario"] == "retry_budget_dlq_recovery"
    )
    assert retry_event["failed_status"] == "failed"
    assert retry_event["attempts_before_replay"] == retry_event["max_retries"]
    assert retry_event["replayed_status"] == "queued"
    assert report["production_ready"] is False
    assert report["passed"] is True
