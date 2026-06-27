from __future__ import annotations

from pathlib import Path

from apps.api.oday_api.main import health_payload
from apps.scheduler.oday_scheduler.main import scheduler_health
from apps.worker.oday_worker.main import worker_health

ROOT = Path(__file__).resolve().parents[2]


def test_developer_command_surface_exists() -> None:
    expected_paths = [
        "Makefile",
        ".github/workflows/ci.yml",
        "docs/development/LOCAL_SETUP.md",
        "pyproject.toml",
        "uv.lock",
    ]

    missing = [path for path in expected_paths if not (ROOT / path).exists()]
    assert missing == []


def test_scaffold_health_payloads_are_stable() -> None:
    assert health_payload() == {"status": "ok", "service": "oday-api"}
    assert worker_health() == {"status": "ok", "service": "oday-worker"}
    assert scheduler_health() == {"status": "ok", "service": "oday-scheduler"}
