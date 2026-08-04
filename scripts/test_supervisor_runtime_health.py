from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime
from pathlib import Path

from supervisor_runtime_health import evaluate_runtime_health


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_health_passes_when_supervisor_lock_and_heartbeat_are_fresh(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=UTC)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {"heartbeat_stale_seconds": 900},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:30Z",
                "lifecycle": "running",
                "last_loop_error": None,
            }
        },
    )
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        report = evaluate_runtime_health(repo, now=now)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is True
    assert report["supervisor"]["lock_held"] is True


def test_health_fails_on_stale_heartbeat(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=UTC)
    write_json(repo / ".orchestrator" / "config.json", {"paths": {"state_file": ".orchestrator/state.json"}})
    write_json(
        repo / ".orchestrator" / "state.json",
        {"supervisor": {"last_heartbeat_at": "2026-06-06T06:00:00Z", "lifecycle": "running"}},
    )

    report = evaluate_runtime_health(repo, now=now, max_heartbeat_age=90)

    assert report["healthy"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "supervisor_process_alive" in failed
    assert "supervisor_heartbeat_fresh" in failed


def test_require_watchdog_fails_when_probe_is_stale(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=UTC)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {"state_file": ".orchestrator/watchdog-state.json"},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {"supervisor": {"last_heartbeat_at": "2026-06-06T06:29:50Z", "lifecycle": "running"}},
    )
    write_json(repo / ".orchestrator" / "watchdog-state.json", {"updated_at": "2026-06-06T06:00:00Z"})
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        report = evaluate_runtime_health(repo, now=now, require_watchdog=True, max_watchdog_age=180)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "watchdog_probe_fresh" in failed


def test_health_checks_dashboard_when_present_and_fresh(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=UTC)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {
                "state_file": ".orchestrator/state.json",
                "status_file": ".orchestrator/ai-status.json",
            },
            "supervisor": {"poll_interval_seconds": 300},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:50Z",
                "lifecycle": "running",
                "pid": 12345,
            }
        },
    )
    write_json(
        repo / ".orchestrator" / "dashboard-bundle.json",
        {
            "generated_at": "2026-06-06T06:29:58Z",
            "runtime_summary": {"supervisor_pid": 12345},
        },
    )
    write_json(
        repo / ".orchestrator" / "ai-status.json",
        {"runtime_summary": {"supervisor_pid": 12345}},
    )

    report = evaluate_runtime_health(repo, now=now)

    # Supervisor process won't exist in test env, but dashboard checks should still be evaluated.
    assert report["healthy"] is False
    names = {item["name"] for item in report["checks"]}
    assert {
        "dashboard_bundle_present",
        "dashboard_bundle_valid",
        "dashboard_bundle_fresh",
        "dashboard_supervisor_pid_matches",
    }.issubset(names)
    assert report["dashboard"]["present"] is True


def test_require_dashboard_fails_when_missing(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=UTC)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json", "status_file": ".orchestrator/ai-status.json"},
            "supervisor": {"poll_interval_seconds": 300},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:50Z",
                "lifecycle": "running",
                "pid": 6789,
            }
        },
    )

    report = evaluate_runtime_health(repo, now=now, require_dashboard=True)

    assert report["healthy"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert {
        "dashboard_bundle_present",
        "dashboard_bundle_valid",
        "dashboard_bundle_fresh",
        "dashboard_supervisor_pid_matches",
    }.issubset(failed)
