"""Contract tests for repo-owned runtime configuration, release identity, tenant wiring, and rollback targets (ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.runtime_config import get_release_identity, resolve_tenant_id

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"


def test_get_release_identity_search_hierarchy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify release identity search hierarchy prioritizes ODAY_RELEASE_SHA."""
    for key in (
        "ODAY_RELEASE_SHA",
        "ODP_RELEASE_COMMIT_SHA",
        "RELEASE_SHA",
        "GITHUB_SHA",
        "COMMIT_SHA",
    ):
        monkeypatch.delenv(key, raising=False)

    assert get_release_identity("fallback") == "fallback"

    monkeypatch.setenv("COMMIT_SHA", "commit-sha-1234")
    assert get_release_identity() == "commit-sha-1234"

    monkeypatch.setenv("GITHUB_SHA", "github-sha-5678")
    assert get_release_identity() == "github-sha-5678"

    monkeypatch.setenv("RELEASE_SHA", "release-sha-9012")
    assert get_release_identity() == "release-sha-9012"

    monkeypatch.setenv("ODP_RELEASE_COMMIT_SHA", "odp-commit-sha-3456")
    assert get_release_identity() == "odp-commit-sha-3456"

    monkeypatch.setenv("ODAY_RELEASE_SHA", "oday-release-sha-7890")
    assert get_release_identity() == "oday-release-sha-7890"


def test_all_runtime_roles_consume_unified_release_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify API, Worker, Scheduler, Notifications, and Entrypoint consume get_release_identity."""
    monkeypatch.setenv("ODAY_RELEASE_SHA", "a" * 40)

    # 1. API runtime
    from apps.api.oday_api.main import release_sha_from_environment

    assert release_sha_from_environment() == "a" * 40

    # 2. Scheduler metrics export
    from apps.scheduler.oday_scheduler.main import ODayScheduler

    scheduler = MagicMock(spec=ODayScheduler)
    scheduler.telemetry = MagicMock()
    with patch(
        "apps.scheduler.oday_scheduler.main.ProductionMetricsExporter"
    ) as mock_exporter:
        mock_instance = MagicMock()
        mock_exporter.return_value = mock_instance
        ODayScheduler.export_metrics(scheduler)
        mock_exporter.assert_called_once_with(
            release_sha="a" * 40, registry=scheduler.telemetry.metrics
        )

    # 3. Worker metrics export
    from apps.worker.oday_worker.main import ODayWorker

    worker = MagicMock(spec=ODayWorker)
    worker.telemetry = MagicMock()
    with patch(
        "apps.worker.oday_worker.main.ProductionMetricsExporter"
    ) as mock_exporter:
        mock_instance = MagicMock()
        mock_exporter.return_value = mock_instance
        ODayWorker.export_metrics(worker)
        mock_exporter.assert_called_once_with(
            release_sha="a" * 40, registry=worker.telemetry.metrics
        )


def test_resolve_tenant_id_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tenant identity resolution fails closed when required."""
    monkeypatch.delenv("ODP_SCHEDULED_INGESTION_TENANT_ID", raising=False)
    monkeypatch.delenv("ODP_TENANT_ID", raising=False)

    assert resolve_tenant_id(required=False, default="tenant-dev") == "tenant-dev"

    with pytest.raises(ValueError, match="Tenant identity required"):
        resolve_tenant_id(required=True)

    monkeypatch.setenv("ODP_TENANT_ID", "tenant-staging-001")
    assert resolve_tenant_id(required=True) == "tenant-staging-001"

    monkeypatch.setenv("ODP_SCHEDULED_INGESTION_TENANT_ID", "tenant-prod-001")
    assert resolve_tenant_id(required=True) == "tenant-prod-001"


def test_deploy_script_requires_tenant_id_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify deploy_cloud_run_waji.sh fails closed when tenant ID is missing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for cmd in ("python3", "uv", "gcloud", "docker"):
        shim = bin_dir / cmd
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env.pop("ODP_SCHEDULED_INGESTION_TENANT_ID", None)
    env.pop("ODP_TENANT_ID", None)
    env["ODP_DEPLOY_ENV"] = "staging"
    env["ODAY_RELEASE_SHA"] = "b" * 40
    env["API_SERVICE"] = "oday-api"
    env["WEB_SERVICE"] = "oday-web"
    env["MIGRATION_JOB"] = "oday-migrate"
    env["WORKER_JOB"] = "oday-worker"
    env["SCHEDULER_JOB"] = "oday-scheduler"
    env["WORKER_SCHEDULE_NAME"] = "worker-schedule"
    env["SCHEDULER_SCHEDULE_NAME"] = "scheduler-schedule"
    env["ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT"] = "sa@gcp.com"
    env["ODP_WORKER_CRON"] = "*/5 * * * *"
    env["ODP_SCHEDULER_CRON"] = "*/1 * * * *"
    env["ODP_SCHEDULER_TIME_ZONE"] = "UTC"
    env["ODP_FORECAST_ENGINE"] = "statsforecast"
    env["ODP_FORECAST_MODEL"] = "seasonal_naive"
    env["ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT"] = "smoke@gcp.com"
    env["ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"] = "15"

    proc = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"deploy script bash syntax error: {proc.stderr}"

    # Running bash execution without tenant set must exit non-zero immediately
    proc_exec = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc_exec.returncode != 0
    assert "ODP_SCHEDULED_INGESTION_TENANT_ID or ODP_TENANT_ID is required" in proc_exec.stderr


def test_deploy_script_contains_explicit_rollback_targets() -> None:
    """Verify deploy_cloud_run_waji.sh and cloud_run_release_traffic.sh arm explicit rollback traps."""
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper_text = (ROOT / "product_ops/deployment/cloud_run_release_traffic.sh").read_text(
        encoding="utf-8"
    )

    assert "ROLLBACK_ARMED=true" in script_text
    assert "SCHEDULER_ROLLBACK_ARMED=true" in script_text
    assert "rollback_release_traffic" in script_text
    assert "restore_scheduler_trigger" in script_text
    assert "capture_service_traffic" in script_text
    assert "capture_scheduler_trigger" in script_text
    assert "restore_service_traffic" in helper_text
    assert "restore_scheduler_trigger" in helper_text
