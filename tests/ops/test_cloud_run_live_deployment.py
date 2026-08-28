from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "product_ops/deployment/validate_cloud_run_live_deployment.py"
TRAFFIC_HELPER_PATH = ROOT / "product_ops/deployment/cloud_run_traffic.py"
TRAFFIC_SHELL_HELPER = ROOT / "product_ops/deployment/cloud_run_release_traffic.sh"
SCHEDULER_HELPER_PATH = ROOT / "product_ops/deployment/cloud_scheduler_trigger.py"
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
WORKFLOWS = (
    ROOT / ".github/workflows/deploy-dev.yml",
)
EXPECTED_SHA = "a" * 40

spec = importlib.util.spec_from_file_location("cloud_run_live_validator", VALIDATOR_PATH)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)

traffic_spec = importlib.util.spec_from_file_location("cloud_run_traffic", TRAFFIC_HELPER_PATH)
assert traffic_spec and traffic_spec.loader
traffic_helper = importlib.util.module_from_spec(traffic_spec)
sys.modules[traffic_spec.name] = traffic_helper
traffic_spec.loader.exec_module(traffic_helper)


def complete_env() -> dict[str, str]:
    env = {name: f"configured-{name.lower()}" for name in validator.REQUIRED_PUBLIC_CONFIG}
    env.update(
        {
            name: f"secret-name-{index}:latest"
            for index, name in enumerate(validator.REQUIRED_SECRET_REFERENCES)
        }
    )
    env.update(validator.REQUIRED_RUNTIME_VALUES)
    env["ODP_DEPLOY_ENV"] = "dev"
    env["ODAY_RELEASE_SHA"] = EXPECTED_SHA
    env["ODP_FORECAST_ENGINE"] = "statsforecast"
    env["ODP_FORECAST_MODEL"] = "seasonal_naive"
    env["ODP_SCHEDULED_INGESTION_TENANT_ID"] = "tenant-dev"
    env["ODP_TENANT_ID"] = "tenant-dev"
    return env


def test_preflight_does_not_require_unselected_listing_partner_config() -> None:
    env = complete_env()
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert all(
        check.ok
        for check in checks
        if check.name.startswith(("config:", "secret-reference:", "runtime:"))
    )
    assert "config:ODP_LISTING_PROVIDER_FEED_URL" not in by_name
    assert "config:ODP_LISTING_PROVIDER_AUTH_STATUS" not in by_name
    assert "secret-reference:ODP_LISTING_PROVIDER_API_KEY_SECRET" not in by_name


def test_preflight_requires_listing_config_when_listing_is_selected() -> None:
    env = {
        "ODP_PRODUCTION_PROVIDER_IDS": "listing.partner_feed",
    }
    checks = validator.selected_provider_config_checks(
        env=env,
        production_provider_ids=frozenset(["listing.partner_feed"]),
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["config:ODP_LISTING_PROVIDER_FEED_URL"].ok is False
    assert by_name["config:ODP_LISTING_PROVIDER_AUTH_STATUS"].ok is False
    assert by_name["secret-reference:ODP_LISTING_PROVIDER_API_KEY_SECRET"].ok is False


def _run_deploy_config_gate(
    tmp_path: Path,
    *,
    forecast_engine: str | None,
    forecast_model: str | None,
) -> subprocess.CompletedProcess[str]:
    for command in ("python3", "uv", "gcloud", "docker"):
        stub = tmp_path / command
        stub.write_text(
            '#!/bin/sh\necho "PREFLIGHT_REACHED" >&2\nexit 97\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

    env = {
        "PATH": str(tmp_path),
        "ODP_DEPLOY_ENV": "staging",
        "ODAY_RELEASE_SHA": EXPECTED_SHA,
        "API_SERVICE": "oday-api",
        "WEB_SERVICE": "oday-web",
        "MIGRATION_JOB": "oday-migrate",
        "WORKER_JOB": "oday-worker",
        "SCHEDULER_JOB": "oday-scheduler",
        "WORKER_SCHEDULE_NAME": "oday-worker-trigger",
        "SCHEDULER_SCHEDULE_NAME": "oday-scheduler-trigger",
        "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT": "scheduler@example.test",
        "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT": "smoke@example.test",
        "ODP_WORKER_CRON": "*/5 * * * *",
        "ODP_SCHEDULER_CRON": "0 * * * *",
        "ODP_SCHEDULER_TIME_ZONE": "Asia/Taipei",
        "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS": "8",
        "ODP_SCHEDULED_INGESTION_TENANT_ID": "tenant-dev",
        "ODP_TENANT_ID": "tenant-dev",
    }
    if forecast_engine is not None:
        env["ODP_FORECAST_ENGINE"] = forecast_engine
    if forecast_model is not None:
        env["ODP_FORECAST_MODEL"] = forecast_model
    return subprocess.run(
        ["/bin/bash", str(DEPLOY_SCRIPT)],
        cwd=ROOT,
        env={**os.environ, **env},
        check=False,
        capture_output=True,
        text=True,
    )


def test_deploy_fails_closed_before_preflight_without_forecast_binding(
    tmp_path: Path,
) -> None:
    result = _run_deploy_config_gate(
        tmp_path,
        forecast_engine=None,
        forecast_model="seasonal_naive",
    )

    assert result.returncode == 1
    assert "ODP_FORECAST_ENGINE is required for live deployments" in result.stderr
    assert "PREFLIGHT_REACHED" not in result.stderr


def test_deploy_fails_closed_before_preflight_for_unsupported_forecast_binding(
    tmp_path: Path,
) -> None:
    result = _run_deploy_config_gate(
        tmp_path,
        forecast_engine="statsforecast",
        forecast_model="invented_model",
    )

    assert result.returncode == 1
    assert "unsupported production ForecastOps binding" in result.stderr
    assert "PREFLIGHT_REACHED" not in result.stderr


def test_deploy_accepts_supported_forecast_binding_and_enters_preflight(
    tmp_path: Path,
) -> None:
    result = _run_deploy_config_gate(
        tmp_path,
        forecast_engine="statsforecast",
        forecast_model="seasonal_naive",
    )

    assert result.returncode == 97
    assert "PREFLIGHT_REACHED" in result.stderr


def test_deploy_script_runs_repository_validators_with_locked_python() -> None:
    """Every validator with repository imports must resolve from uv.lock."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "for cmd in python3 uv gcloud docker; do" in text
    assert 'uv run --frozen python "$@"' in text
    for invocation in (
        "run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py preflight",
        "run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py jobs-smoke",
        "run_locked_python "
        "product_ops/deployment/validate_cloud_run_live_deployment.py compatibility-smoke",
        "run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py smoke",
        "run_locked_python delivery_toolchain/e2e/check_live_e2e_gate.py",
    ):
        assert invocation in text

    assert "python3 product_ops/deployment/validate_cloud_run_live_deployment.py" not in text
    assert "python3 delivery_toolchain/e2e/check_live_e2e_gate.py" not in text
    assert text.count("python3 - ") == 2
    assert text.count("imports only Python's standard library") == 2


def test_deploy_preflight_imports_runtime_dependencies_via_locked_python(
    tmp_path: Path,
) -> None:
    """Reproduce run 30331484524 with bare Python blocked.

    The failing run had already synced uv.lock, but the deploy script called its
    preflight with system ``python3`` and could not import httpx or pydantic.
    This harness makes any non-inline system-Python call fail explicitly; the
    real preflight must still pass before the stubbed gcloud boundary stops the
    deployment.
    """
    python_stub = tmp_path / "python3"
    python_stub.write_text(
        "#!/bin/sh\n"
        'if [ "${1:-}" != "-" ]; then\n'
        '  echo "BARE_PYTHON_VALIDATOR_INVOKED" >&2\n'
        "  exit 86\n"
        "fi\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    python_stub.chmod(0o755)

    uv_stub = tmp_path / "uv"
    uv_stub.write_text(
        "#!/bin/sh\n"
        'if [ "${1:-}" = "run" ]; then\n'
        "  shift\n"
        "  while [ $# -gt 0 ]; do\n"
        '    case "$1" in\n'
        "      --frozen|--no-sync|python) shift ;;\n"
        "      *) break ;;\n"
        "    esac\n"
        "  done\n"
        f'  exec "{sys.executable}" "$@"\n'
        "fi\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    uv_stub.chmod(0o755)

    for command in ("gcloud", "docker"):
        stub = tmp_path / command
        stub.write_text(
            '#!/bin/sh\necho "LOCKED_PREFLIGHT_REACHED_GCLOUD" >&2\nexit 97\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

    report_path = tmp_path / "preflight.json"
    env = complete_env()
    env.update(
        {
            "PATH": f"{tmp_path}{os.pathsep}{Path.home() / '.local' / 'bin'}{os.pathsep}{ROOT / '.venv' / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "API_SERVICE": "oday-api",
            "WEB_SERVICE": "oday-web",
            "MIGRATION_JOB": "oday-migrate",
            "WORKER_JOB": "oday-worker",
            "SCHEDULER_JOB": "oday-scheduler",
            "WORKER_SCHEDULE_NAME": "oday-worker-trigger",
            "SCHEDULER_SCHEDULE_NAME": "oday-scheduler-trigger",
            "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT": "scheduler@example.test",
            "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT": "smoke@example.test",
            "ODP_WORKER_CRON": "*/5 * * * *",
            "ODP_SCHEDULER_CRON": "0 * * * *",
            "ODP_SCHEDULER_TIME_ZONE": "Asia/Taipei",
            "PREFLIGHT_REPORT": str(report_path),
            "JOB_REPORT_DIR": str(tmp_path / "job-reports"),
        }
    )
    result = subprocess.run(
        ["/bin/bash", str(DEPLOY_SCRIPT)],
        cwd=ROOT,
        env={**os.environ, **env},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 97
    assert "BARE_PYTHON_VALIDATOR_INVOKED" not in result.stderr
    assert "LOCKED_PREFLIGHT_REACHED_GCLOUD" in result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    checks = {check["name"]: check for check in report["checks"]}
    assert "repository:provider_registry_import" not in checks
    assert checks["repository:operator_bootstrap_data_source"]["ok"] is True


def test_preflight_reports_current_repository_runtime_capabilities() -> None:
    checks = validator.preflight_checks(
        env=complete_env(),
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert "repository:production_database_adapter" in by_name
    assert by_name["repository:worker_runtime"].ok is True
    assert by_name["repository:scheduler_runtime"].ok is True
    assert by_name["repository:migration_runtime"].ok is True
    assert by_name["repository:release_traffic"].ok is True
    assert by_name["repository:provider_allowlist_runtime"].ok is True
    # The live-required operator runtime is correctly fail-closed today: an
    # absent repository raises instead of exposing seed rows, and the real
    # production composition injects the live repository.
    bootstrap_check = by_name["repository:operator_bootstrap_data_source"]
    assert bootstrap_check.ok is True
    assert "fail-closed" in bootstrap_check.detail
    assert by_name["repository:operator_production_wiring"].ok is True
    assert by_name["repository:operator_fixture_wiring_blocked"].ok is True
    assert by_name["repository:operator_tenant_scope_fail_closed"].ok is True
    assert by_name["repository:operator_live_probe_contract"].ok is True


class _FakeRepositoryError(RuntimeError):
    pass


def _fake_operator_state_module(
    *,
    payload: dict[str, object] | None = None,
    error: Exception | None = None,
) -> SimpleNamespace:
    class _FakeOperatorStateService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        @property
        def data_origin(self) -> dict[str, object]:
            return {"kind": "unavailable", "sourceId": None}

        def get_today(self, **_kwargs: object) -> dict[str, object] | None:
            if error is not None:
                raise error
            return payload

    return SimpleNamespace(
        OperatorLiveRepositoryError=_FakeRepositoryError,
        OperatorStateService=_FakeOperatorStateService,
    )


def _fake_main_module(operator_live_repository: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        create_app=lambda **_kwargs: SimpleNamespace(
            state=SimpleNamespace(operator_live_repository=operator_live_repository)
        )
    )


def test_operator_checks_block_seed_exposure_but_allow_fail_closed_unavailable() -> None:
    seed_payload = {
        "meta": {
            "dataMode": "fixture",
            "dataOrigin": {"kind": "fixture", "sourceId": "r4-seed"},
        },
        "workQueue": [{"id": "task-seed-1"}],
        "approvals": [],
    }
    checks = validator.operator_runtime_checks(
        ROOT,
        operator_state_module=_fake_operator_state_module(payload=seed_payload),
        main_module=_fake_main_module(),
    )
    by_name = {check.name: check for check in checks}
    seed_check = by_name["repository:operator_bootstrap_data_source"]
    assert seed_check.ok is False
    assert "seed/fixture" in seed_check.detail

    unavailable_payload = {
        "meta": {
            "dataMode": "unavailable",
            "dataOrigin": {"kind": "unavailable", "sourceId": None},
        },
        "workQueue": [],
        "approvals": [],
    }
    checks = validator.operator_runtime_checks(
        ROOT,
        operator_state_module=_fake_operator_state_module(payload=unavailable_payload),
        main_module=_fake_main_module(),
    )
    by_name = {check.name: check for check in checks}
    unavailable_check = by_name["repository:operator_bootstrap_data_source"]
    assert unavailable_check.ok is True
    assert "fail-closed" in unavailable_check.detail

    raising_checks = validator.operator_runtime_checks(
        ROOT,
        operator_state_module=_fake_operator_state_module(
            error=_FakeRepositoryError("Operator live repository is not configured")
        ),
        main_module=_fake_main_module(),
    )
    raising_by_name = {check.name: check for check in raising_checks}
    raising_check = raising_by_name["repository:operator_bootstrap_data_source"]
    assert raising_check.ok is True
    assert "not seed exposure" in raising_check.detail


def test_operator_wiring_check_requires_injected_live_repository() -> None:
    checks = validator.operator_runtime_checks(ROOT, main_module=_fake_main_module())
    by_name = {check.name: check for check in checks}
    assert by_name["repository:operator_production_wiring"].ok is False
    assert "did not inject" in by_name["repository:operator_production_wiring"].detail
    assert by_name["repository:operator_fixture_wiring_blocked"].ok is True

    checks = validator.operator_runtime_checks(
        ROOT, main_module=_fake_main_module(operator_live_repository=object())
    )
    by_name = {check.name: check for check in checks}
    assert by_name["repository:operator_production_wiring"].ok is False
    assert by_name["repository:operator_fixture_wiring_blocked"].ok is False


def test_preflight_imports_every_registry_provider_adapter() -> None:
    checks = validator.provider_adapter_checks(ROOT)
    by_name = {check.name: check for check in checks}

    # Standing live-required set: geocode / poi / admin_boundary.
    assert by_name["repository:provider_adapter:geocode.primary_api"].ok is True
    assert by_name["repository:provider_adapter:poi.commercial_api"].ok is True
    assert (
        "PoiCommercialApiProvider"
        in by_name["repository:provider_adapter:poi.commercial_api"].detail
    )
    assert by_name["repository:provider_adapter:admin_boundary.official_dataset"].ok is True
    assert (
        "AdminBoundaryDatasetProvider"
        in by_name["repository:provider_adapter:admin_boundary.official_dataset"].detail
    )
    # listing.partner_feed is a ready-but-not-required bulk capability; its concrete
    # adapter must still import when a licensed partner is gated into the allowlist.
    listing_checks = validator.provider_adapter_checks(
        ROOT, production_provider_ids=frozenset({"listing.partner_feed"})
    )
    listing_by_name = {check.name: check for check in listing_checks}
    assert listing_by_name["repository:provider_adapter:listing.partner_feed"].ok is True
    assert "repository:provider_adapter:competitor.manual_source" not in by_name


def test_preflight_rejects_manual_competitor_in_production_allowlist() -> None:
    env = complete_env()
    env["ODP_PRODUCTION_PROVIDER_IDS"] = (
        ",".join(sorted(validator.REQUIRED_PRODUCT_PROVIDER_IDS)) + ",competitor.manual_source"
    )
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["runtime:production_provider_licenses"].ok is False
    assert "competitor.manual_source" in by_name["runtime:production_provider_licenses"].detail
    assert by_name["runtime:competitor_manual_disabled"].ok is False
    assert "repository:provider_adapter:competitor.manual_source" not in by_name


def test_preflight_requires_all_product_provider_ids_and_disabled_manual_status() -> None:
    env = complete_env()
    env["ODP_PRODUCTION_PROVIDER_IDS"] = "listing.partner_feed,geocode.primary_api"
    env["ODP_COMPETITOR_MANUAL_SOURCE_STATUS"] = "active"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["runtime:required_product_providers"].ok is False
    assert "poi.commercial_api" in by_name["runtime:required_product_providers"].detail
    assert by_name["runtime:ODP_COMPETITOR_MANUAL_SOURCE_STATUS"].ok is False


def test_preflight_rejects_missing_config_memory_and_live_provider_mode() -> None:
    env = complete_env()
    env["GCP_PROJECT"] = ""
    env["ODP_PERSISTENCE"] = "memory"
    env["ODP_EXTERNAL_PROVIDER_MODE"] = "live"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["config:GCP_PROJECT"].ok is False
    assert by_name["runtime:ODP_PERSISTENCE"].ok is False
    assert by_name["runtime:external_provider_mode_off"].ok is False


class DeterministicRuntimeHandler(BaseHTTPRequestHandler):
    release_sha = EXPECTED_SHA
    data_mode = "live"
    database_mode = "postgresql"
    operator_source = "postgresql"
    operator_repository_ready = True
    operator_origin_kind = "authoritative"
    missing_provider_id = ""
    failed_provider_id = ""
    probe_age_seconds = 0

    @classmethod
    def _operator_origin(cls) -> dict[str, object]:
        return {
            "kind": cls.operator_origin_kind,
            "sourceId": (
                "operator-live-repository"
                if cls.operator_origin_kind == "authoritative"
                else "r4-seed"
            ),
            "repository": "OperatorLiveRepository",
            "persistenceMode": cls.database_mode,
        }

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/platform/version":
            self._json({"status": "ok", "release_sha": self.release_sha})
            return
        if self.path == "/platform/health":
            checked_at = datetime.now(UTC) - timedelta(seconds=self.probe_age_seconds)
            expires_at = checked_at + timedelta(seconds=60)
            required_provider_ids = sorted(validator.REQUIRED_PRODUCT_PROVIDER_IDS)
            probes = [
                {
                    "provider_id": provider_id,
                    "configuration_valid": True,
                    "connectivity_healthy": (provider_id != self.failed_provider_id),
                    "authentication_accepted": (provider_id != self.failed_provider_id),
                    "response_valid": provider_id != self.failed_provider_id,
                    "schema_valid": provider_id != self.failed_provider_id,
                    "checked_at": checked_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "latency_ms": 2,
                    "http_status": (401 if provider_id == self.failed_provider_id else 200),
                    "reason_code": (
                        "unauthorized" if provider_id == self.failed_provider_id else "ok"
                    ),
                }
                for provider_id in required_provider_ids
                if provider_id != self.missing_provider_id
            ]
            connectivity_healthy = not (self.missing_provider_id or self.failed_provider_id)
            self._json(
                {
                    "status": "ok",
                    "data_mode": self.data_mode,
                    "dependencies": {
                        "database": {"status": "healthy", "mode": self.database_mode},
                        "job_queue": {"status": "healthy", "mode": "cloud-run-worker"},
                        "external_providers": {
                            "status": ("healthy" if connectivity_healthy else "unhealthy"),
                            "mode": "live",
                            "configuration_valid": True,
                            "connectivity_healthy": connectivity_healthy,
                            "checked_at": checked_at.isoformat(),
                            "expires_at": expires_at.isoformat(),
                            "required_provider_ids": required_provider_ids,
                            "probes": probes,
                        },
                    },
                }
            )
            return
        if self.path == "/readiness":
            self._json(
                {
                    "status": "ok",
                    "data_mode": self.data_mode,
                    "details": {
                        "database": {"status": "healthy", "mode": self.database_mode},
                        "data": {
                            "mode": self.data_mode,
                            "liveReady": self.operator_repository_ready,
                            "operatorRepositoryReady": self.operator_repository_ready,
                            "operatorRepositoryProbe": {
                                "ready": self.operator_repository_ready,
                                "checkedAt": datetime.now(UTC).isoformat(),
                                "repository": "OperatorLiveRepository",
                                "persistenceMode": self.database_mode,
                                "errors": (
                                    []
                                    if self.operator_repository_ready
                                    else ["OperatorLiveRepositoryError: stores: connection refused"]
                                ),
                            },
                            "origin": self._operator_origin(),
                        },
                    },
                }
            )
            return
        if self.path == "/api/v1/operator/bootstrap":
            if self.headers.get("authorization") != "Bearer smoke-token":
                self._json({"detail": "unauthorized"}, status=401)
                return
            self._json(
                {
                    "meta": {
                        "dataMode": self.data_mode,
                        "dataSource": self.operator_source,
                        "dataOrigin": self._operator_origin(),
                        "liveReadiness": {
                            "ready": self.operator_repository_ready,
                            "reasonCode": (
                                "OPERATOR_LIVE_REPOSITORY_READY"
                                if self.operator_repository_ready
                                else "OPERATOR_LIVE_REPOSITORY_UNAVAILABLE"
                            ),
                        },
                    },
                    "today": {"queue": []},
                }
            )
            return
        if self.path == "/operator":
            self.send_response(307)
            self.send_header(
                "location",
                f"http://{self.headers['host']}/login?returnTo=%2Foperator",
            )
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def _json(self, payload: dict[str, object], *, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        DeterministicRuntimeHandler,
    )
    Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def run_smoke(url: str):
    return validator.smoke_checks(
        api_url=url,
        web_url=url,
        expected_sha=EXPECTED_SHA,
        bearer_token="smoke-token",
        operator_role="ops-lead",
        operator_subject="smoke-operator",
        operator_tenant="tenant-live",
        correlation_id="corr-cloud-run-test",
        timeout=2,
    )


def test_deterministic_smoke_contract_requires_fresh_provider_probe_evidence() -> None:
    server, url = start_server()
    try:
        checks, report = run_smoke(url)
    finally:
        server.shutdown()

    assert all(check.ok for check in checks)
    assert report["version"]["release_sha"] == EXPECTED_SHA
    assert report["operator_bootstrap"]["data_mode"] == "live"
    assert report["secret_values_redacted"] is True


def test_deterministic_smoke_rejects_stale_or_incomplete_provider_evidence() -> None:
    DeterministicRuntimeHandler.probe_age_seconds = 600
    DeterministicRuntimeHandler.missing_provider_id = "poi.commercial_api"
    server, url = start_server()
    try:
        checks, _ = run_smoke(url)
    finally:
        server.shutdown()
        DeterministicRuntimeHandler.probe_age_seconds = 0
        DeterministicRuntimeHandler.missing_provider_id = ""

    failed = {check.name for check in checks if not check.ok}
    assert "smoke:/platform/health:external_providers:completeness" in failed
    assert "smoke:/platform/health:external_providers:freshness" in failed
    assert "smoke:/platform/health:external_providers:poi.commercial_api" in failed


def test_is_safe_protected_redirect_contract() -> None:
    web_url = "https://candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app"

    # Absolute HTTPS safe redirect
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"{web_url}/login?returnTo=%2Foperator"
    ) is True

    # Relative safe redirect
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=%2Foperator"
    ) is True

    # Hostile scheme downgrade rejection (HTTPS base -> HTTP target must fail)
    http_web_url = "http://candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app"
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"{http_web_url}/login?returnTo=%2Foperator"
    ) is False

    # Hostile port mismatch rejection (default port 443 vs nondefault port 8443)
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"{web_url}:8443/login?returnTo=%2Foperator"
    ) is False

    # Malformed non-numeric port rejection (must fail closed, not raise ValueError)
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"{web_url}:bad/login?returnTo=%2Foperator"
    ) is False

    # Out-of-range port rejection (must fail closed, not raise ValueError)
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"{web_url}:99999/login?returnTo=%2Foperator"
    ) is False

    # Hostile userinfo rejection
    assert validator._is_safe_protected_redirect(
        web_url, 307, "https://user:pass@candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app/login?returnTo=%2Foperator"
    ) is False

    # Hostile fragment rejection
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"{web_url}/login?returnTo=%2Foperator#hostile-fragment"
    ) is False

    # Hostile external returnTo parameter rejection
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=https%3A%2F%2Fattacker.com"
    ) is False
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=%2Fevil-path"
    ) is False
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=%2Foperator%2Fextra"
    ) is False
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=%252Foperator"
    ) is False

    # Hostile external origin rejection
    assert validator._is_safe_protected_redirect(
        web_url, 307, "https://attacker.com/login?returnTo=%2Foperator"
    ) is False

    # Hostile protocol-relative origin rejection
    assert validator._is_safe_protected_redirect(
        web_url, 307, "//attacker.com/login?returnTo=%2Foperator"
    ) is False

    # Fail-closed: 200 OK (no redirect performed)
    assert validator._is_safe_protected_redirect(
        web_url, 200, None
    ) is False

    # Fail-closed: 200 OK carrying an otherwise valid login Location. Only the
    # status guard can reject this, unlike the (200, None) case above.
    assert validator._is_safe_protected_redirect(
        web_url, 200, "/login?returnTo=%2Foperator"
    ) is False

    # Hostile scheme downgrade at a matching effective port. Only the scheme
    # guard can reject this, unlike the http:// case above (port 80 vs 443).
    assert validator._is_safe_protected_redirect(
        web_url,
        307,
        "http://candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app:443"
        "/login?returnTo=%2Foperator",
    ) is False

    # Redirect to wrong target path
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/dashboard?returnTo=%2Foperator"
    ) is False

    # Redirect to /login without returnTo parameter
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login"
    ) is False


def test_is_safe_protected_redirect_requires_a_redirect_status() -> None:
    """The status guard alone must reject an otherwise perfect Location.

    Mutation target: dropping ``web_status not in {302, 303, 307, 308}`` must
    fail this test. A 200 with a valid login Location means the protected page
    was rendered to an unauthenticated caller, which is exactly the fail-closed
    condition the smoke check exists to catch.
    """

    web_url = "https://candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app"
    safe_location = "/login?returnTo=%2Foperator"

    assert validator._is_safe_protected_redirect(web_url, 200, safe_location) is False
    assert validator._is_safe_protected_redirect(web_url, 301, safe_location) is False
    assert validator._is_safe_protected_redirect(web_url, 403, safe_location) is False
    for status in (302, 303, 307, 308):
        assert validator._is_safe_protected_redirect(web_url, status, safe_location) is True


def test_is_safe_protected_redirect_rejects_scheme_downgrade_at_matching_port() -> None:
    """The scheme guard alone must reject an http:// target on port 443.

    Mutation target: dropping the scheme comparison must fail this test. The
    pre-existing ``http://<host>/login`` case is killed by the effective-port
    guard (80 != 443), so it does not exercise the scheme comparison at all.
    """

    host = "candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app"
    web_url = f"https://{host}"

    assert validator._is_safe_protected_redirect(
        web_url, 307, f"http://{host}:443/login?returnTo=%2Foperator"
    ) is False
    # Control: identical URL over https is accepted, so the rejection above is
    # attributable to the scheme and nothing else.
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"https://{host}:443/login?returnTo=%2Foperator"
    ) is True


def test_is_safe_protected_redirect_accepts_default_ports_and_padded_headers() -> None:
    """Correct default ports and header padding must not fail a valid redirect.

    Both assertions are false-negative guards: nothing else in this file pins
    them, so a wrong http default port or a dropped ``strip()`` would silently
    turn a healthy candidate deploy red.
    """

    host = "candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app"

    # Mutation target: _effective_port's http default. Every other case in this
    # file uses an https base, where both sides get the same default, so only an
    # http origin with an explicit :80 distinguishes 80 from any other value.
    assert validator._is_safe_protected_redirect(
        f"http://{host}:80", 307, "/login?returnTo=%2Foperator"
    ) is True
    assert validator._is_safe_protected_redirect(
        f"http://{host}", 307, f"http://{host}:80/login?returnTo=%2Foperator"
    ) is True

    # Mutation target: the location.strip() before urljoin. Surrounding
    # whitespace is legal header framing, and urlsplit does not strip a trailing
    # run inside the query, so an unstripped value loses the returnTo match.
    assert validator._is_safe_protected_redirect(
        f"https://{host}", 307, "  /login?returnTo=%2Foperator  "
    ) is True


def test_is_safe_protected_redirect_rejects_ambiguous_or_unparsable_locations() -> None:
    """Cardinality, userinfo-only, empty and unparsable Locations fail closed."""

    host = "candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app"
    web_url = f"https://{host}"

    # Duplicate returnTo: the intended target is ambiguous, so reject.
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=%2Foperator&returnTo=%2Foperator"
    ) is False
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=%2Foperator&returnTo=%2Fevil-path"
    ) is False

    # A blank first returnTo smuggles the real one past a parser that drops
    # empty values: without keep_blank_values this collapses to a single valid
    # returnTo and the ambiguity guard never fires.
    assert validator._is_safe_protected_redirect(
        web_url, 307, "/login?returnTo=&returnTo=%2Foperator"
    ) is False

    # Bare "@" delimiter with empty username/password still signals userinfo.
    assert validator._is_safe_protected_redirect(
        web_url, 307, f"https://@{host}/login?returnTo=%2Foperator"
    ) is False

    # Missing / empty / whitespace-only Location header on a redirect status.
    assert validator._is_safe_protected_redirect(web_url, 307, None) is False
    assert validator._is_safe_protected_redirect(web_url, 307, "") is False
    assert validator._is_safe_protected_redirect(web_url, 307, "   ") is False

    # Unparsable IPv6 literal must be caught, not raised.
    assert validator._is_safe_protected_redirect(
        web_url, 307, "https://[::1/login?returnTo=%2Foperator"
    ) is False

    # A base web_url without a usable origin fails closed rather than matching
    # a same-shaped relative Location.
    assert validator._is_safe_protected_redirect(
        "candidate-host-without-scheme", 307, "/login?returnTo=%2Foperator"
    ) is False
    assert validator._is_safe_protected_redirect(
        "", 307, "/login?returnTo=%2Foperator"
    ) is False

    # Only http/https origins have a defined effective port, so a non-web
    # scheme fails closed even when scheme, host and path all agree.
    assert validator._is_safe_protected_redirect(
        f"ftp://{host}", 307, f"ftp://{host}/login?returnTo=%2Foperator"
    ) is False

    # A base with a scheme but no host: base and target hostnames are both
    # empty, so only the explicit empty-host guard rejects this.
    assert validator._is_safe_protected_redirect(
        "https:", 307, "/login?returnTo=%2Foperator"
    ) is False

    # A base with a host and an explicit port but no scheme: host and effective
    # port both match, so only the explicit empty-scheme guard rejects this.
    assert validator._is_safe_protected_redirect(
        f"//{host}:8443", 307, "/login?returnTo=%2Foperator"
    ) is False


def test_redact_location_masks_credentials_and_parameter_values() -> None:
    """Reports advertise secret_values_redacted, so Location must be sanitised."""

    host = "candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app"

    # The real candidate shape stays fully readable for diagnosis.
    assert validator._redact_location("/login?returnTo=%2Foperator") == (
        "/login?returnTo=%2Foperator"
    )

    # Userinfo credentials never reach the report.
    redacted = validator._redact_location(
        f"https://svc-account:hunter2@{host}/login?returnTo=%2Foperator"
    )
    assert redacted == f"https://<redacted>@{host}/login?returnTo=%2Foperator"
    assert "hunter2" not in redacted
    assert "svc-account" not in redacted

    # Non-returnTo parameter values are masked (session/bearer material).
    redacted = validator._redact_location(
        "/login?returnTo=%2Foperator&session=super-secret-token&code=abc123"
    )
    assert redacted == "/login?returnTo=%2Foperator&session=<redacted>&code=<redacted>"
    assert "super-secret-token" not in redacted
    assert "abc123" not in redacted

    # A hostile returnTo is not echoed verbatim either.
    redacted = validator._redact_location("/login?returnTo=https%3A%2F%2Fattacker.com")
    assert redacted == "/login?returnTo=<redacted>"
    assert "attacker.com" not in redacted

    # Each _is_plain_relative_path clause is pinned separately, since only the
    # clause named in the comment rejects its case.
    # Protocol-relative: starts with "/" and carries no "://".
    redacted = validator._redact_location("/login?returnTo=%2F%2Fattacker.com")
    assert redacted == "/login?returnTo=<redacted>"
    assert "attacker.com" not in redacted
    # Embedded scheme after a leading single slash: not caught by the "//" test.
    assert validator._redact_location("/login?returnTo=%2Fa%3A%2F%2Fb") == (
        "/login?returnTo=<redacted>"
    )
    # Non-printable payload: passes both textual tests, fails only isprintable.
    assert validator._redact_location("/login?returnTo=%2Fop%0Aerator") == (
        "/login?returnTo=<redacted>"
    )

    # Fragments are dropped to a marker; their presence stays diagnosable.
    assert validator._redact_location(
        f"https://{host}/login?returnTo=%2Foperator#token=leak"
    ) == f"https://{host}/login?returnTo=%2Foperator#<redacted>"

    # Missing / malformed inputs render as markers instead of raising.
    assert validator._redact_location(None) == "<missing>"
    assert validator._redact_location("   ") == "<missing>"
    assert validator._redact_location(f"https://{host}:bad/login") == (
        f"https://{host}:<invalid-port>/login"
    )
    assert validator._redact_location("https://[::1/login") == "<unparsable>"


def test_smoke_report_never_carries_a_raw_location_header(monkeypatch) -> None:
    """smoke_checks() must publish only the redacted Location."""

    hostile_location = (
        "https://svc:hunter2@candidate-host.example/login"
        "?returnTo=%2Foperator&session=super-secret-token"
    )

    def fake_request_without_redirect(url, *, headers, timeout):
        return 307, hostile_location

    def offline_json_request(url, *, headers, timeout):
        raise OSError("network disabled in this test")

    monkeypatch.setattr(
        validator, "_request_without_redirect", fake_request_without_redirect
    )
    monkeypatch.setattr(validator, "_json_request", offline_json_request)

    checks, report = validator.smoke_checks(
        api_url="https://api.invalid",
        web_url="https://candidate-host.example",
        expected_sha=None,
        bearer_token="token",
        operator_role="operator",
        operator_subject="subject",
        operator_tenant="tenant",
        correlation_id="corr-1",
        timeout=0.01,
    )

    redirect_report = report["web_operator_redirect"]
    assert report["secret_values_redacted"] is True
    assert "location" not in redirect_report
    assert redirect_report["protected_redirect"] is False
    assert redirect_report["location_redacted"] == (
        "https://<redacted>@candidate-host.example/login"
        "?returnTo=%2Foperator&session=<redacted>"
    )

    serialized = json.dumps(
        {
            "checks": [[check.ok, check.name, check.detail] for check in checks],
            "report": report,
        }
    )
    assert "hunter2" not in serialized
    assert "super-secret-token" not in serialized


def test_deterministic_smoke_rejects_provider_specific_auth_failure() -> None:
    DeterministicRuntimeHandler.failed_provider_id = "geocode.primary_api"
    server, url = start_server()
    try:
        checks, _ = run_smoke(url)
    finally:
        server.shutdown()
        DeterministicRuntimeHandler.failed_provider_id = ""

    by_name = {check.name: check for check in checks}
    provider_check = by_name["smoke:/platform/health:external_providers:geocode.primary_api"]
    assert provider_check.ok is False
    assert provider_check.detail == ("provider probe failed: reason_code=unauthorized")


def test_migration_compatibility_smoke_only_requires_old_database_compatibility() -> None:
    DeterministicRuntimeHandler.release_sha = "b" * 40
    DeterministicRuntimeHandler.data_mode = "fixture"
    server, url = start_server()
    try:
        checks, _ = validator.compatibility_smoke_checks(
            api_url=url,
            web_url=url,
            correlation_id="corr-cloud-run-compat-test",
            timeout=2,
        )
    finally:
        server.shutdown()
        DeterministicRuntimeHandler.release_sha = EXPECTED_SHA
        DeterministicRuntimeHandler.data_mode = "live"

    assert all(check.ok for check in checks)
    names = {check.name for check in checks}
    assert "compatibility:/platform/version:http" in names
    assert "compatibility:/platform/health:database" in names
    assert not any("external_providers" in name for name in names)


# --- migration compatibility probe: bounded cold-start retry contract --------
#
# Deploy Dev run 30402570022 failed this gate because the old revision was
# scaled to zero and took 28.1s to answer a single 15.0s attempt. The retry
# contract exists to outlast that cold start and nothing else: every outcome
# the old revision actually answers is a verdict and must still fail closed on
# the first response.


def _attempt(
    *,
    status: int | None = None,
    payload: dict | None = None,
    error: str = "",
    elapsed: float = 0.0,
    provenance: str | None = None,
) -> object:
    # Convenience only: the authoritative classification of a real response is
    # exercised end-to-end through probe_json_endpoint below, never inferred.
    if provenance is None:
        if status is None:
            provenance = validator.PROBE_NO_RESPONSE
        elif payload is not None:
            provenance = validator.PROBE_JSON_OBJECT
        else:
            provenance = validator.PROBE_UNPARSEABLE_BODY
    return validator.ProbeAttempt(
        status=status,
        payload=payload,
        error=error,
        elapsed_seconds=elapsed,
        provenance=provenance,
    )


def _timeout_attempt(elapsed: float = 15.0) -> object:
    return _attempt(error="The read operation timed out", elapsed=elapsed)


def _healthy_version() -> object:
    return _attempt(status=200, payload={"status": "ok", "release_sha": EXPECTED_SHA}, elapsed=1.9)


def _health_attempt(database: object, *, status: int = 503) -> object:
    dependencies: dict[str, object] = {"job_queue": "healthy"}
    if database is not None:
        dependencies["database"] = database
    return _attempt(
        status=status, payload={"status": "unhealthy", "dependencies": dependencies}, elapsed=0.9
    )


class _ScriptedProbe:
    """Replays a scripted attempt sequence per endpoint and records timeouts."""

    def __init__(self, *, version: list, health: list) -> None:
        self._scripts = {"/platform/version": list(version), "/platform/health": list(health)}
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, *, headers, timeout: float):
        path = "/platform/health" if url.endswith("/platform/health") else "/platform/version"
        self.calls.append((path, timeout))
        script = self._scripts[path]
        return script.pop(0) if len(script) > 1 else script[0]

    def attempts_for(self, path: str) -> int:
        return sum(1 for called_path, _ in self.calls if called_path == path)


class _RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class _FakeClock:
    """Monotonic clock advanced explicitly by the fake probe and fake sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _run_compatibility(probe: _ScriptedProbe, monkeypatch) -> tuple[list, dict, _RecordingSleep]:
    sleeper = _RecordingSleep()
    monkeypatch.setattr(validator, "probe_json_endpoint", probe)
    checks, report = validator.compatibility_smoke_checks(
        api_url="https://oday-api.example",
        web_url="https://oday-web.example",
        correlation_id="corr-cloud-run-compat-test",
        timeout=15.0,
        sleep=sleeper,
    )
    return checks, report, sleeper


def test_probe_retry_policy_backoff_is_bounded_and_exponential() -> None:
    policy = validator.ProbeRetryPolicy(
        attempts=6, timeout_seconds=15.0, backoff_seconds=2.0, max_backoff_seconds=8.0
    )
    assert [policy.backoff_for(index) for index in range(1, 7)] == [0.0, 2.0, 4.0, 8.0, 8.0, 8.0]


@pytest.mark.parametrize(
    "attempts,timeout,backoff,deadline",
    [(0, 15.0, 2.0, 120.0), (4, 0.0, 2.0, 120.0), (4, 15.0, -1.0, 120.0), (4, 15.0, 2.0, 0.0)],
)
def test_probe_retry_policy_rejects_unbounded_or_degenerate_configuration(
    attempts: int, timeout: float, backoff: float, deadline: float
) -> None:
    with pytest.raises(ValueError):
        validator.ProbeRetryPolicy(
            attempts=attempts,
            timeout_seconds=timeout,
            backoff_seconds=backoff,
            deadline_seconds=deadline,
        )


@pytest.mark.parametrize("field", ["timeout_seconds", "backoff_seconds", "deadline_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_probe_retry_policy_rejects_non_finite_bounds(field: str, value: float) -> None:
    # NaN slips past every `<= 0` guard and infinity is not a deadline, so both
    # must raise here -- the one place the CLI turns into a fail-closed report.
    with pytest.raises(ValueError, match="finite"):
        validator.ProbeRetryPolicy(**{field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_probe_retry_policy_rejects_non_finite_max_backoff_and_attempts(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        validator.ProbeRetryPolicy(max_backoff_seconds=value)
    with pytest.raises(ValueError, match="finite"):
        validator.ProbeRetryPolicy(attempts=value)


def test_probe_failure_is_transient_only_when_no_response_was_received() -> None:
    # Nothing came back: the cold start this contract exists for.
    assert validator.probe_failure_is_transient(_timeout_attempt()) is True
    assert (
        validator.probe_failure_is_transient(
            _attempt(error="<urlopen error [Errno 104] Connection reset by peer>")
        )
        is True
    )
    # Any received response is final on attempt 1, whatever it carried. A
    # status code is not proof that the Cloud Run front end -- rather than the
    # old revision itself -- produced the response, so it never buys a retry.
    for status in (200, 404, 408, 429, 500, 502, 503, 504):
        assert validator.probe_failure_is_transient(_attempt(status=status, payload={})) is False
        assert (
            validator.probe_failure_is_transient(
                _attempt(
                    status=status,
                    error="did not return valid JSON",
                    provenance=validator.PROBE_UNPARSEABLE_BODY,
                )
            )
            is False
        ), status
        assert (
            validator.probe_failure_is_transient(
                _attempt(
                    status=status,
                    error="returned a non-object JSON payload",
                    provenance=validator.PROBE_NON_OBJECT_BODY,
                )
            )
            is False
        ), status
    # No response either, but nothing was sent: a URL urllib refuses to turn
    # into a request cannot be fixed by rebuilding the identical request.
    assert (
        validator.probe_failure_is_transient(
            _attempt(
                error="is not a requestable URL",
                provenance=validator.PROBE_INVALID_REQUEST,
            )
        )
        is False
    )


def test_probe_attempt_rejects_a_provenance_that_contradicts_the_response() -> None:
    # A received-but-unreadable response cannot be recorded as no-response.
    with pytest.raises(ValueError):
        validator.ProbeAttempt(
            status=503,
            payload=None,
            error="did not return valid JSON",
            elapsed_seconds=0.1,
            provenance=validator.PROBE_NO_RESPONSE,
        )
    with pytest.raises(ValueError):
        validator.ProbeAttempt(
            status=None,
            payload=None,
            error="timed out",
            elapsed_seconds=0.1,
            provenance=validator.PROBE_UNPARSEABLE_BODY,
        )
    with pytest.raises(ValueError):
        validator.ProbeAttempt(
            status=200,
            payload={"status": "ok"},
            error="",
            elapsed_seconds=0.1,
            provenance=validator.PROBE_UNPARSEABLE_BODY,
        )
    with pytest.raises(ValueError):
        validator.ProbeAttempt(
            status=200, payload=None, error="", elapsed_seconds=0.1, provenance="maybe"
        )
    # A request that was never built has no status to record.
    with pytest.raises(ValueError):
        validator.ProbeAttempt(
            status=503,
            payload=None,
            error="is not a requestable URL",
            elapsed_seconds=0.1,
            provenance=validator.PROBE_INVALID_REQUEST,
        )


@pytest.mark.parametrize(
    "body,expected_provenance,expected_error",
    [
        ("<html>503 Service Unavailable</html>", validator.PROBE_UNPARSEABLE_BODY, "valid JSON"),
        ("", validator.PROBE_UNPARSEABLE_BODY, "valid JSON"),
        ("[]", validator.PROBE_NON_OBJECT_BODY, "non-object"),
        ('"unavailable"', validator.PROBE_NON_OBJECT_BODY, "non-object"),
    ],
)
def test_probe_json_endpoint_classifies_a_503_body_as_a_received_response(
    body: str, expected_provenance: str, expected_error: str, monkeypatch
) -> None:
    # HTTP 503 with a body the old revision's JSON contract does not satisfy.
    # `payload is None` here as well as on a timeout -- provenance is what
    # keeps the two apart.
    monkeypatch.setattr(validator, "_request", lambda url, **_: (503, "text/html", body))
    attempt = validator.probe_json_endpoint(
        "https://oday-api.example/platform/version", headers={}, timeout=1.0
    )

    assert attempt.status == 503
    assert attempt.payload is None
    assert attempt.provenance == expected_provenance
    assert attempt.response_received is True
    assert attempt.has_verdict is False
    assert expected_error in attempt.error
    assert validator.probe_failure_is_transient(attempt) is False


@pytest.mark.parametrize(
    "body,expected_provenance",
    [
        ("<html>503 Service Unavailable</html>", validator.PROBE_UNPARSEABLE_BODY),
        ("[]", validator.PROBE_NON_OBJECT_BODY),
    ],
)
def test_compatibility_probe_rejects_a_503_unreadable_body_on_attempt_one(
    body: str, expected_provenance: str, monkeypatch
) -> None:
    """End-to-end: a 503 with an unreadable body must not spend a retry."""

    requests: list[str] = []

    def fake_request(url: str, *, headers, timeout: float):
        requests.append(url)
        return 503, "text/html", body

    sleeper = _RecordingSleep()
    monkeypatch.setattr(validator, "_request", fake_request)
    checks, report = validator.compatibility_smoke_checks(
        api_url="https://oday-api.example",
        web_url="https://oday-web.example",
        correlation_id="corr-cloud-run-compat-test",
        timeout=15.0,
        sleep=sleeper,
    )

    assert not any(check.ok for check in checks)
    assert sleeper.delays == []
    assert requests == [
        "https://oday-api.example/platform/version",
        "https://oday-api.example/platform/health",
    ]
    for probe in ("version_probe", "health_probe"):
        assert report[probe]["outcome"] == "rejected", probe
        assert report[probe]["attempt_count"] == 1, probe
        assert report[probe]["attempts"][0]["transient"] is False, probe
        assert report[probe]["attempts"][0]["provenance"] == expected_provenance, probe
        assert report[probe]["attempts"][0]["status"] == 503, probe
    # No verdict was recorded, so the gate cannot claim database compatibility.
    assert "version" not in report
    assert "health" not in report


def test_compatibility_probe_recovers_from_a_bounded_cold_start(monkeypatch) -> None:
    probe = _ScriptedProbe(
        version=[_timeout_attempt(), _timeout_attempt(), _healthy_version()],
        health=[_health_attempt("healthy")],
    )
    checks, report, sleeper = _run_compatibility(probe, monkeypatch)

    assert all(check.ok for check in checks), [c for c in checks if not c.ok]
    assert probe.attempts_for("/platform/version") == 3
    assert probe.attempts_for("/platform/health") == 1
    assert sleeper.delays == [2.0, 4.0]
    assert report["version_probe"]["attempt_count"] == 3
    assert report["version_probe"]["outcome"] == "answered"
    assert report["health_probe"]["attempt_count"] == 1
    assert report["probe_retry_policy"] == {
        "attempts": 4,
        "per_attempt_timeout_seconds": 15.0,
        "backoff_seconds": 2.0,
        "max_backoff_seconds": 8.0,
        "total_deadline_seconds": 120.0,
    }
    assert report["secret_values_redacted"] is True


def test_compatibility_probe_fails_closed_when_attempts_are_exhausted(monkeypatch) -> None:
    probe = _ScriptedProbe(version=[_timeout_attempt()], health=[_timeout_attempt()])
    checks, report, _ = _run_compatibility(probe, monkeypatch)

    assert not any(check.ok for check in checks)
    assert probe.attempts_for("/platform/version") == validator.COMPATIBILITY_PROBE_ATTEMPTS
    assert probe.attempts_for("/platform/health") == validator.COMPATIBILITY_PROBE_ATTEMPTS
    assert report["version_probe"]["outcome"] == "attempts_exhausted"
    assert report["health_probe"]["outcome"] == "attempts_exhausted"
    by_name = {check.name: check.detail for check in checks}
    assert "The read operation timed out" in by_name["compatibility:/platform/version:http"]
    assert "attempts_exhausted" in by_name["compatibility:/platform/health:database"]
    assert "version" not in report
    assert "health" not in report


@pytest.mark.parametrize(
    "version_attempt_kwargs,expected_detail,expected_outcome",
    [
        ({"status": 500, "payload": {"status": "error"}}, "status=500", "answered"),
        ({"status": 404, "payload": {"detail": "not found"}}, "status=404", "answered"),
        ({"status": 200, "error": "did not return valid JSON: line 1"}, "valid JSON", "rejected"),
        (
            {
                "status": 200,
                "error": "returned a non-object JSON payload",
                "provenance": validator.PROBE_NON_OBJECT_BODY,
            },
            "non-object",
            "rejected",
        ),
    ],
)
def test_compatibility_version_verdicts_fail_closed_without_retry(
    version_attempt_kwargs: dict, expected_detail: str, expected_outcome: str, monkeypatch
) -> None:
    probe = _ScriptedProbe(
        version=[_attempt(**version_attempt_kwargs)], health=[_health_attempt("healthy")]
    )
    checks, report, sleeper = _run_compatibility(probe, monkeypatch)

    by_name = {check.name: check for check in checks}
    version_check = by_name["compatibility:/platform/version:http"]
    assert version_check.ok is False
    assert expected_detail in version_check.detail
    assert probe.attempts_for("/platform/version") == 1
    assert sleeper.delays == []
    assert report["version_probe"]["outcome"] == expected_outcome


@pytest.mark.parametrize(
    "database",
    ["unhealthy", None, "healthy (sqlite)", "degraded", {"status": "unhealthy"}],
)
def test_compatibility_database_verdicts_fail_closed_without_retry(
    database: object, monkeypatch
) -> None:
    probe = _ScriptedProbe(version=[_healthy_version()], health=[_health_attempt(database)])
    checks, _, sleeper = _run_compatibility(probe, monkeypatch)

    by_name = {check.name: check for check in checks}
    assert by_name["compatibility:/platform/version:http"].ok is True
    database_check = by_name["compatibility:/platform/health:database"]
    assert database_check.ok is False
    assert probe.attempts_for("/platform/health") == 1
    assert sleeper.delays == []


def test_compatibility_health_non_probeable_status_fails_closed(monkeypatch) -> None:
    probe = _ScriptedProbe(
        version=[_healthy_version()],
        health=[_health_attempt("healthy", status=500)],
    )
    checks, _, _ = _run_compatibility(probe, monkeypatch)

    by_name = {check.name: check for check in checks}
    assert by_name["compatibility:/platform/health:database"].ok is False


def test_probe_retry_stops_on_the_total_deadline_before_the_attempt_cap() -> None:
    clock = _FakeClock()
    sleeper = _RecordingSleep()

    def fake_sleep(seconds: float) -> None:
        sleeper(seconds)
        clock.advance(seconds)

    def fake_probe(url: str, *, headers, timeout: float):
        clock.advance(timeout)
        return _timeout_attempt(elapsed=timeout)

    result = validator.probe_with_bounded_retry(
        "https://oday-api.example/platform/version",
        headers={},
        policy=validator.ProbeRetryPolicy(
            attempts=10,
            timeout_seconds=15.0,
            backoff_seconds=2.0,
            max_backoff_seconds=8.0,
            deadline_seconds=40.0,
        ),
        probe=fake_probe,
        monotonic=clock,
        sleep=fake_sleep,
    )

    assert result.exhausted == "deadline_exhausted"
    assert len(result.attempts) == 2
    assert clock.now <= 40.0
    assert result.final.has_verdict is False


def test_probe_retry_clamps_the_attempt_timeout_to_the_remaining_deadline() -> None:
    clock = _FakeClock()
    seen: list[float] = []

    def fake_probe(url: str, *, headers, timeout: float):
        seen.append(timeout)
        clock.advance(timeout)
        return _timeout_attempt(elapsed=timeout)

    result = validator.probe_with_bounded_retry(
        "https://oday-api.example/platform/version",
        headers={},
        policy=validator.ProbeRetryPolicy(
            attempts=3, timeout_seconds=15.0, backoff_seconds=1.0, deadline_seconds=5.0
        ),
        probe=fake_probe,
        monotonic=clock,
        sleep=lambda seconds: clock.advance(seconds),
    )

    assert seen == [5.0]
    assert result.exhausted == "deadline_exhausted"
    assert clock.now <= 5.0


def test_probe_json_endpoint_never_raises_and_classifies_the_transport(monkeypatch) -> None:
    def boom(url: str, *, headers, timeout: float):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(validator, "_request", boom)
    attempt = validator.probe_json_endpoint(
        "https://oday-api.example/platform/version", headers={}, timeout=1.0
    )
    assert attempt.status is None
    assert attempt.payload is None
    assert attempt.error == "The read operation timed out"
    assert validator.probe_failure_is_transient(attempt) is True


# Two malformed HTTPS URLs whose failures escape the `OSError` arm of
# `probe_json_endpoint`, from two unrelated exception families. Neither reaches
# the network: `urllib.parse` and `http.client` both refuse before any socket
# is opened, which is also why a retry could never change the outcome.
UNREQUESTABLE_URLS = [
    ("https://[::1", "Invalid IPv6 URL"),  # ValueError, raised building Request
    ("https://exa mple.invalid", "control characters"),  # http.client.InvalidURL
]


@pytest.mark.parametrize("url,expected_error", UNREQUESTABLE_URLS)
def test_probe_json_endpoint_rejects_a_url_it_cannot_request(url: str, expected_error: str) -> None:
    # Regression: a malformed --api-url used to escape probe_json_endpoint as an
    # unhandled ValueError / InvalidURL, so the gate died with a traceback and
    # wrote no compatibility report -- no verdict for the deploy script to act
    # on. It must instead be a non-retryable, received-nothing attempt.
    attempt = validator.probe_json_endpoint(f"{url}/platform/version", headers={}, timeout=15.0)

    assert attempt.provenance == validator.PROBE_INVALID_REQUEST
    assert attempt.status is None
    assert attempt.payload is None
    assert attempt.response_received is False
    assert attempt.has_verdict is False
    assert expected_error in attempt.error
    assert validator.probe_failure_is_transient(attempt) is False


@pytest.mark.parametrize("url,expected_error", UNREQUESTABLE_URLS)
def test_probe_retry_spends_one_attempt_and_no_backoff_on_an_unrequestable_url(
    url: str, expected_error: str
) -> None:
    sleeper = _RecordingSleep()
    result = validator.probe_with_bounded_retry(
        f"{url}/platform/version",
        headers={},
        policy=validator.ProbeRetryPolicy(
            attempts=4, timeout_seconds=15.0, backoff_seconds=2.0, deadline_seconds=120.0
        ),
        sleep=sleeper,
    )

    assert len(result.attempts) == 1
    assert result.exhausted == ""
    assert result.outcome == "rejected"
    assert sleeper.delays == []
    assert expected_error in result.final.error
    assert result.as_report()["attempts"][0]["transient"] is False


@pytest.mark.parametrize("url,_expected_error", UNREQUESTABLE_URLS)
def test_compatibility_smoke_fails_closed_on_an_unrequestable_api_url(
    url: str, _expected_error: str
) -> None:
    sleeper = _RecordingSleep()
    checks, report = validator.compatibility_smoke_checks(
        api_url=url,
        web_url="https://oday-web.example",
        correlation_id="corr-cloud-run-compat-badurl",
        timeout=15.0,
        sleep=sleeper,
    )

    assert [check.ok for check in checks] == [False, False]
    assert sleeper.delays == []
    for probe in ("version_probe", "health_probe"):
        assert report[probe]["outcome"] == "rejected"
        assert report[probe]["attempt_count"] == 1
        assert report[probe]["attempts"][0]["provenance"] == validator.PROBE_INVALID_REQUEST
        assert report[probe]["attempts"][0]["transient"] is False
    # No verdict was received, so none may be recorded.
    assert "version" not in report
    assert "health" not in report


def test_compatibility_database_verdict_is_not_a_substring_match() -> None:
    # "unhealthy" contains "healthy": a substring probe would pass the exact
    # verdict this gate exists to catch.
    assert validator._database_reads_healthy({"dependencies": {"database": "unhealthy"}}) is False
    assert validator._database_reads_healthy({"dependencies": {"database": "healthy"}}) is True
    assert validator._database_reads_healthy({"details": {"database": "HEALTHY "}}) is True
    assert (
        validator._database_reads_healthy({"dependencies": {"database": {"status": "healthy"}}})
        is True
    )
    assert (
        validator._database_reads_healthy({"dependencies": {"database": {"status": "unhealthy"}}})
        is False
    )
    assert validator._database_reads_healthy({"dependencies": {}}) is False
    assert validator._database_reads_healthy({}) is False
    assert (
        validator._database_reads_healthy({"dependencies": {"database": "healthy (sqlite)"}})
        is False
    )


def test_compatibility_smoke_cli_records_the_bounded_retry_contract(tmp_path: Path) -> None:
    DeterministicRuntimeHandler.release_sha = "b" * 40
    server, url = start_server()
    report_path = tmp_path / "cloud-run-migration-compatibility.json"
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR_PATH),
                "compatibility-smoke",
                "--api-url",
                url,
                "--web-url",
                url,
                "--correlation-id",
                "corr-cloud-run-compat-cli-test",
                "--timeout",
                "5",
                "--compat-retry-attempts",
                "3",
                "--compat-retry-backoff-seconds",
                "1",
                "--compat-retry-max-backoff-seconds",
                "4",
                "--compat-retry-deadline-seconds",
                "30",
                "--output",
                str(report_path),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        DeterministicRuntimeHandler.release_sha = EXPECTED_SHA

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["probe_retry_policy"] == {
        "attempts": 3,
        "per_attempt_timeout_seconds": 5.0,
        "backoff_seconds": 1.0,
        "max_backoff_seconds": 4.0,
        "total_deadline_seconds": 30.0,
    }
    assert report["version_probe"]["attempt_count"] == 1
    assert report["health_probe"]["outcome"] == "answered"
    assert report["secret_values_redacted"] is True


def test_compatibility_smoke_cli_fails_closed_on_an_unbounded_retry_policy(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "cloud-run-migration-compatibility.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "compatibility-smoke",
            "--api-url",
            "http://127.0.0.1:1",
            "--web-url",
            "http://127.0.0.1:1",
            "--compat-retry-attempts",
            "0",
            "--output",
            str(report_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["checks"][0]["name"] == "compatibility:retry_policy"


@pytest.mark.parametrize(
    "flag",
    [
        "--timeout",
        "--compat-retry-backoff-seconds",
        "--compat-retry-max-backoff-seconds",
        "--compat-retry-deadline-seconds",
    ],
)
@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_compatibility_smoke_cli_fails_closed_on_a_non_finite_bound(
    tmp_path: Path, flag: str, value: str
) -> None:
    # Regression for `--timeout nan`, which used to reach the socket layer and
    # die with an unhandled ValueError -- no report, no rollback signal, just a
    # traceback the deploy gate cannot interpret as a compatibility verdict.
    report_path = tmp_path / f"cloud-run-migration-compatibility{flag}{value}.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "compatibility-smoke",
            "--api-url",
            "http://127.0.0.1:1",
            "--web-url",
            "http://127.0.0.1:1",
            # `=` form: bare `-inf` would be parsed as an option string.
            f"{flag}={value}",
            "--output",
            str(report_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["checks"][0]["name"] == "compatibility:retry_policy"
    assert "finite" in report["checks"][0]["detail"]


@pytest.mark.parametrize("url,expected_error", UNREQUESTABLE_URLS)
def test_compatibility_smoke_cli_fails_closed_on_an_unrequestable_url(
    tmp_path: Path, url: str, expected_error: str
) -> None:
    # Regression for `--api-url https://[::1`, which used to die inside
    # probe_json_endpoint with an unhandled ValueError: exit 2, a traceback, and
    # no report file at all, so the deploy gate had no compatibility verdict.
    report_path = tmp_path / "cloud-run-migration-compatibility.json"
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "compatibility-smoke",
            "--api-url",
            url,
            "--web-url",
            url,
            "--correlation-id",
            "corr-cloud-run-compat-cli-badurl",
            "--output",
            str(report_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    # The default policy is in force: 4 attempts with a 2s backoff would have
    # slept at least 14s had the rejection been classified as transient.
    assert report["probe_retry_policy"]["attempts"] == 4
    assert report["probe_retry_policy"]["backoff_seconds"] == 2.0
    assert elapsed < 10.0
    for probe in ("version_probe", "health_probe"):
        assert report[probe]["outcome"] == "rejected"
        assert report[probe]["attempt_count"] == 1
        assert report[probe]["attempts"][0]["provenance"] == "invalid_request"
        assert report[probe]["attempts"][0]["transient"] is False
    assert all(expected_error in check["detail"] for check in report["checks"])
    assert {check["name"] for check in report["checks"]} == {
        "compatibility:/platform/version:http",
        "compatibility:/platform/health:database",
    }


def test_migration_compatibility_gate_wires_bounded_retry_flags() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    gate = text[text.index("run_migration_compatibility_gate() {") :]
    gate = gate[: gate.index("\n}\n")]
    for flag in (
        '--timeout "${MIGRATION_COMPAT_TIMEOUT}"',
        '--compat-retry-attempts "${MIGRATION_COMPAT_RETRY_ATTEMPTS}"',
        '--compat-retry-backoff-seconds "${MIGRATION_COMPAT_RETRY_BACKOFF}"',
        '--compat-retry-max-backoff-seconds "${MIGRATION_COMPAT_RETRY_MAX_BACKOFF}"',
        '--compat-retry-deadline-seconds "${MIGRATION_COMPAT_RETRY_DEADLINE}"',
    ):
        assert flag in gate
    assert 'MIGRATION_COMPAT_RETRY_ATTEMPTS="${MIGRATION_COMPAT_RETRY_ATTEMPTS:-4}"' in text
    assert 'MIGRATION_COMPAT_RETRY_DEADLINE="${MIGRATION_COMPAT_RETRY_DEADLINE:-120}"' in text
    # The gate must still run before any candidate service is deployed.
    assert text.index("run_migration_compatibility_gate\n") < text.index(
        'gcloud run deploy "${API_SERVICE}"'
    )


def test_deterministic_smoke_rejects_wrong_sha_memory_and_seed_operator() -> None:
    DeterministicRuntimeHandler.release_sha = "b" * 40
    DeterministicRuntimeHandler.data_mode = "fixture"
    DeterministicRuntimeHandler.database_mode = "in-memory"
    DeterministicRuntimeHandler.operator_source = "canonical-r4-seed"
    DeterministicRuntimeHandler.operator_origin_kind = "fixture"
    server, url = start_server()
    try:
        checks, _ = run_smoke(url)
    finally:
        server.shutdown()
        DeterministicRuntimeHandler.release_sha = EXPECTED_SHA
        DeterministicRuntimeHandler.data_mode = "live"
        DeterministicRuntimeHandler.database_mode = "postgresql"
        DeterministicRuntimeHandler.operator_source = "postgresql"
        DeterministicRuntimeHandler.operator_origin_kind = "authoritative"

    failed = {check.name for check in checks if not check.ok}
    assert "smoke:/platform/version:release_sha" in failed
    assert "smoke:/platform/health:live_data_mode" in failed
    assert "smoke:/platform/health:database" in failed
    assert "smoke:/readiness:database" in failed
    assert "smoke:/readiness:operator_live_repository" in failed
    assert "smoke:/api/v1/operator/bootstrap:provenance" in failed
    assert "smoke:/api/v1/operator/bootstrap:read_provenance" in failed


def test_deterministic_smoke_requires_ready_operator_live_repository_probe() -> None:
    DeterministicRuntimeHandler.operator_repository_ready = False
    server, url = start_server()
    try:
        checks, _ = run_smoke(url)
    finally:
        server.shutdown()
        DeterministicRuntimeHandler.operator_repository_ready = True

    by_name = {check.name: check for check in checks}
    readiness_check = by_name["smoke:/readiness:operator_live_repository"]
    assert readiness_check.ok is False
    assert "probe" in readiness_check.detail
    provenance_check = by_name["smoke:/api/v1/operator/bootstrap:read_provenance"]
    assert provenance_check.ok is False
    # An unavailable repository blocks candidate promotion, but the failure is
    # reported as a failing probe, not as seed exposure.
    assert by_name["smoke:/api/v1/operator/bootstrap:provenance"].ok is True


def test_workflows_do_not_reference_secrets_in_step_if() -> None:
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        if_lines = [line for line in text.splitlines() if line.strip().startswith("if:")]
        assert all("secrets." not in line for line in if_lines)
        # Federated identity used to be asserted here as `env.HAS_WIF`, a
        # var-derived boolean that also gated the auth steps via `if:`. In a
        # job with no `environment:` binding that boolean is always false, so
        # the guard skipped authentication instead of refusing. The presence
        # check now runs as a fail-closed step inside each bound job.
        assert "delivery_toolchain/release/check_release_environment.py" in text
        assert "env.HAS_WIF" not in text
        assert "GCP_SA_KEY" not in text
        assert "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT" in text
        assert 'ODP_REQUIRE_LIVE_DATA: "true"' in text
        assert "ODP_DATA_BINDING_MODE: live" in text
        assert "ODP_PERSISTENCE: postgresql" in text
        assert "ODP_FORECAST_ENGINE: ${{ vars.ODP_FORECAST_ENGINE }}" in text
        assert "ODP_FORECAST_MODEL: ${{ vars.ODP_FORECAST_MODEL }}" in text
        assert "ODP_CLOUD_RUN_MIGRATION_JOB" in text
        assert "ODP_CLOUD_RUN_WORKER_JOB" in text
        assert "ODP_CLOUD_RUN_SCHEDULER_JOB" in text
        assert "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT" in text
        assert "ODP_WORKER_CRON" in text
        assert "ODP_SCHEDULER_CRON" in text
        assert "ODP_PRODUCTION_PROVIDER_IDS" not in text
        assert "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS" not in text
        assert "ODP_EXTERNAL_PROVIDER_MODE" in text
        assert "ODP_EXTERNAL_PROVIDER_MODE: disabled" in text
        assert "ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled" in text
        assert "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION_SECRET" not in text
        assert "validate_cloud_run_live_deployment.py preflight" in text
        assert "ODP_OPERATOR_SMOKE_BEARER_TOKEN" not in text
        assert "ODP_AUTH_JWKS_URI" in text
        assert "ODP_POI_PROVIDER_URL" not in text
        assert "ODP_ADMIN_BOUNDARY_PROVIDER_URL" not in text
        assert "ODP_LISTING_PROVIDER_FEED_URL" not in text
        assert "ODP_GEOCODE_PROVIDER_URL" not in text
        assert "ODP_LISTING_PROVIDER_API_KEY_SECRET" not in text
        assert "ODP_POI_PROVIDER_API_KEY_SECRET" not in text
        assert "ODP_GEOCODE_PROVIDER_API_KEY_SECRET" not in text
        assert "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN_SECRET" not in text
        assert "ODP_WEB_OIDC_CLIENT_ID" in text
        assert "ODP_WEB_OIDC_CLIENT_SECRET_SECRET" in text
        assert "ODP_WEB_SESSION_SECRET_SECRET" in text


def test_dev_workflow_bootstraps_locked_dependencies_before_preflight() -> None:
    """ODP-DEPLOY-PREFLIGHT-CONFIG-001: the preflight's repository capability
    checks import the real provider registry (httpx and friends), so the deploy
    job must materialize the locked project environment before the preflight
    runs — and must run the preflight inside that environment, not on the
    runner's bare system python3 where the import fails and the deploy dies on
    a dependency error instead of a real gate.
    """
    text = (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")

    sync = text.index("uv sync --frozen")
    preflight = text.index("validate_cloud_run_live_deployment.py preflight")
    assert sync < preflight
    assert (
        "uv run --frozen python "
        "product_ops/deployment/validate_cloud_run_live_deployment.py preflight" in text
    )
    assert "python3 product_ops/deployment/validate_cloud_run_live_deployment.py" not in text


def test_deploy_script_preflights_before_build_and_uses_secret_references() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert text.index("validate_cloud_run_live_deployment.py preflight") < text.index(
        "docker build"
    )
    assert text.index('execute_job "migration" "${MIGRATION_CANDIDATE_JOB}"') < text.index(
        'gcloud run deploy "${API_SERVICE}"'
    )
    assert 'gcloud run jobs deploy "${MIGRATION_CANDIDATE_JOB}"' in text
    assert 'gcloud run jobs deploy "${WORKER_CANDIDATE_JOB}"' in text
    assert 'gcloud run jobs deploy "${SCHEDULER_CANDIDATE_JOB}"' in text
    assert 'execute_job "worker" "${WORKER_CANDIDATE_JOB}"' in text
    assert 'execute_job "scheduler" "${SCHEDULER_CANDIDATE_JOB}"' in text
    assert "gcloud scheduler jobs" in text
    assert "jobs-smoke" in text
    assert "validate_cloud_run_live_deployment.py smoke" in text
    assert "validate_cloud_run_live_deployment.py compatibility-smoke" in text
    assert text.count("--no-traffic") == 2
    assert text.count('--revision-suffix="${REVISION_SUFFIX}"') == 2
    assert '--tag="${API_REVISION_TAG}"' in text
    assert '--tag="${WEB_REVISION_TAG}"' in text
    assert "handle_deployment_exit" in text
    assert "rollback_release_traffic" in text
    assert text.index('capture_service_traffic "${API_SERVICE}"') < text.index(
        'gcloud run jobs deploy "${MIGRATION_CANDIDATE_JOB}"'
    )
    migration_gate = text.rindex("run_migration_compatibility_gate")
    candidate_smoke = text.index("validate_cloud_run_live_deployment.py smoke")
    api_cut = text.index('promote_service_traffic "${API_SERVICE}"')
    web_cut = text.index('promote_service_traffic "${WEB_SERVICE}"')
    committed = text.index("DEPLOYMENT_COMMITTED=true")
    assert migration_gate < candidate_smoke < api_cut < web_cut < committed
    assert '--set-secrets="${API_SECRET_BINDINGS}"' in text
    assert '--set-secrets="${WEB_SECRET_BINDINGS}"' in text
    assert "ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}" in text
    assert "ODP_WEB_OIDC_CLIENT_SECRET=${ODP_WEB_OIDC_CLIENT_SECRET_SECRET}" in text
    assert "ODP_WEB_SESSION_SECRET=${ODP_WEB_SESSION_SECRET_SECRET}" in text
    assert "ODP_AUTH_PRINCIPAL_MAP=${ODP_AUTH_PRINCIPAL_MAP_SECRET}" in text
    assert '--impersonate-service-account="${ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT}"' in text
    assert "::add-mask::${ODP_OPERATOR_SMOKE_BEARER_TOKEN}" in text
    assert "ODAY_RELEASE_SHA" in text
    assert "ODP_REQUIRE_LIVE_DATA" in text
    assert "ODP_PERSISTENCE" in text
    assert '"ODP_POI_PROVIDER_URL"' not in text
    assert '"ODP_ADMIN_BOUNDARY_PROVIDER_URL"' not in text
    assert 'case "${provider_id}" in' not in text
    assert ': "${ODP_FORECAST_ENGINE:?' in text
    assert ': "${ODP_FORECAST_MODEL:?' in text
    assert '"ODP_FORECAST_ENGINE",' in text
    assert '"ODP_FORECAST_MODEL",' in text
    assert ('API_SERVICE_AUDIENCE="$(service_snapshot_url "${API_CANDIDATE_DESCRIPTION}")"') in text
    assert ('python3 - "${WEB_ENV_FILE}" "${API_URL}" "${API_SERVICE_AUDIENCE}" <<\'PY\'') in text
    assert '"ODP_API_BASE_URL": sys.argv[2],' in text
    assert '"ODP_API_SERVICE_AUDIENCE": sys.argv[3],' in text
    assert text.count('--env-vars-file="${API_ENV_FILE}"') == 4
    assert 'gcloud run jobs deploy "${MIGRATION_CANDIDATE_JOB}"' in text
    assert 'gcloud run jobs deploy "${SCHEDULER_CANDIDATE_JOB}"' in text
    assert 'gcloud run jobs deploy "${WORKER_CANDIDATE_JOB}"' in text
    assert text.index("validate_cloud_run_live_deployment.py smoke") < text.index(
        'upsert_scheduler_trigger \\\n  "${SCHEDULER_SCHEDULE_NAME}"'
    )
    assert "restore_scheduler_trigger" in text
    assert "ODP_PRODUCTION_PROVIDER_IDS" not in text
    assert "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS" not in text
    assert '"ODP_EXTERNAL_PROVIDER_MODE",' in text
    assert "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION" not in text
    assert "oday-local" not in text
    assert "postgresql://" not in text

    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _traffic_description(
    *,
    service_url: str = "https://service.example.test",
    traffic: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "status": {
            "url": service_url,
            "traffic": traffic
            or [
                {"revisionName": "service-old-a", "percent": 80},
                {"revisionName": "service-old-b", "percent": 20},
            ],
        }
    }


def test_traffic_helper_resolves_tagged_revision_and_exact_restore_split() -> None:
    description = _traffic_description(
        traffic=[
            {"revisionName": "service-old", "percent": 100},
            {
                "revisionName": "service-release-abc123",
                "percent": 0,
                "tag": "candidate-abc123",
                "url": "https://candidate-abc123.example.test",
            },
        ]
    )

    assert traffic_helper.tagged_target(description, "candidate-abc123") == (
        "service-release-abc123",
        "https://candidate-abc123.example.test",
    )
    assert traffic_helper.restore_traffic_argument(description) == "service-old=100"


def test_traffic_helper_records_an_explicit_absent_bootstrap_snapshot() -> None:
    snapshot = traffic_helper.absent_snapshot("oday-api")

    assert snapshot == {
        "schema_version": 1,
        "kind": "cloud-run-service-traffic-snapshot",
        "exists": False,
        "service": "oday-api",
    }
    assert traffic_helper.is_present(snapshot) is False
    assert traffic_helper.service_url(snapshot) == ""
    assert traffic_helper.is_present(_traffic_description()) is True


def test_bootstrap_compatibility_cli_writes_not_applicable_receipt(tmp_path: Path) -> None:
    report_path = tmp_path / "cloud-run-bootstrap-compatibility.json"
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "bootstrap-compatibility",
            "--environment",
            "dev",
            "--release-sha",
            EXPECTED_SHA,
            "--output",
            str(report_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["bootstrap"] is True
    assert report["previous_services"] == {"api": "absent", "web": "absent"}
    assert report["secret_values_redacted"] is True


def test_rollback_attempts_both_services_and_returns_nonzero_on_partial_failure(
    tmp_path: Path,
) -> None:
    api_snapshot = tmp_path / "api.json"
    web_snapshot = tmp_path / "web.json"
    api_snapshot.write_text(json.dumps(_traffic_description()), encoding="utf-8")
    web_snapshot.write_text(json.dumps(_traffic_description()), encoding="utf-8")

    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >>"${GCLOUD_LOG}"
if [ "${4:-}" = "${FAIL_SERVICE:-}" ]; then
  exit 17
fi
""",
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)

    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        'rollback_release_traffic "api-service" '
        f'"{api_snapshot}" "web-service" "{web_snapshot}"\n'
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GCLOUD_LOG": str(gcloud_log),
        "FAIL_SERVICE": "api-service",
        "GCP_PROJECT": "test-project",
        "GCP_REGION": "test-region",
        "ODP_TRAFFIC_HELPER": str(TRAFFIC_HELPER_PATH),
    }
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    calls = gcloud_log.read_text(encoding="utf-8").splitlines()
    assert any("update-traffic api-service" in call for call in calls)
    assert any("update-traffic web-service" in call for call in calls)
    assert all("--to-revisions=service-old-a=80,service-old-b=20" in call for call in calls)


def test_bootstrap_rollback_deletes_service_that_was_previously_absent(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "api.json"
    snapshot.write_text(
        json.dumps(traffic_helper.absent_snapshot("api-service")),
        encoding="utf-8",
    )

    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >>"${GCLOUD_LOG}"
if [ "${1:-} ${2:-} ${3:-}" = "run services list" ]; then
  printf '%s\n' "api-service"
fi
""",
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)

    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'restore_service_traffic "api-service" "{snapshot}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_TRAFFIC_HELPER": str(TRAFFIC_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gcloud_log.read_text(encoding="utf-8").splitlines()
    assert any("run services list" in call for call in calls)
    assert any("run services delete api-service" in call for call in calls)
    assert all("update-traffic" not in call for call in calls)


def test_scheduler_trigger_restore_uses_recorded_target_and_schedule(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "scheduler.json"
    snapshot.write_text(
        json.dumps(
            {
                "schedule": "17 * * * *",
                "timeZone": "Asia/Taipei",
                "httpTarget": {
                    "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/old-job:run",
                    "oauthToken": {
                        "serviceAccountEmail": "scheduler@example.test",
                        "scope": "https://www.googleapis.com/auth/cloud-platform",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/usr/bin/env bash\n'
        'printf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n'
        'if [[ "$*" == *"scheduler jobs describe"* && "$*" == *"--format=json"* ]]; then\n'
        f'  cat "{snapshot}"\n'
        'fi\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'restore_scheduler_trigger "worker-trigger" "{snapshot}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_SCHEDULER_HELPER": str(SCHEDULER_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    call = gcloud_log.read_text(encoding="utf-8")
    assert "scheduler jobs update http worker-trigger" in call
    assert "--schedule=17 * * * *" in call
    assert "/jobs/old-job:run" in call


def test_scheduler_trigger_restore_supports_oidc_token(tmp_path: Path) -> None:
    snapshot = tmp_path / "scheduler.json"
    snapshot.write_text(
        json.dumps(
            {
                "schedule": "0 * * * *",
                "timeZone": "Asia/Taipei",
                "state": "ENABLED",
                "httpTarget": {
                    "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/worker-job:run",
                    "httpMethod": "POST",
                    "headers": {
                        "Content-Type": "application/json",
                        "User-Agent": "Google-Cloud-Scheduler",
                    },
                    "body": "e30=",
                    "oidcToken": {
                        "serviceAccountEmail": "scheduler-sa@example.test",
                        "audience": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/worker-job:run",
                    },
                },
                "retryConfig": {
                    "maxRetryAttempts": 3,
                    "maxRetryDuration": "1800s",
                    "minBackoffDuration": "10s",
                    "maxBackoffDuration": "600s",
                    "maxDoublings": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/usr/bin/env bash\n'
        'printf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n'
        'if [[ "$*" == *"scheduler jobs describe"* && "$*" == *"--format=json"* ]]; then\n'
        f'  cat "{snapshot}"\n'
        'fi\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'restore_scheduler_trigger "worker-trigger" "{snapshot}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_SCHEDULER_HELPER": str(SCHEDULER_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    call = gcloud_log.read_text(encoding="utf-8")
    assert "--oidc-service-account-email=scheduler-sa@example.test" in call
    assert "--oidc-token-audience=https://run.googleapis.com/v2/projects/p/locations/r/jobs/worker-job:run" in call
    assert "--max-retry-attempts=3" in call
    assert "--min-backoff=10s" in call
    assert "--max-backoff=600s" in call
    # gcloud keeps only the last occurrence of the header dict flag, so every
    # restored header must arrive as one comma-separated map.
    assert (
        "--update-headers=Content-Type=application/json,User-Agent=Google-Cloud-Scheduler"
        in call
    )
    assert "--headers=Content-Type=application/json" not in call


def test_scheduler_trigger_restore_handles_paused_state(tmp_path: Path) -> None:
    snapshot = tmp_path / "scheduler.json"
    snapshot.write_text(
        json.dumps(
            {
                "schedule": "0 12 * * *",
                "timeZone": "UTC",
                "state": "PAUSED",
                "httpTarget": {
                    "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/scheduler-job:run",
                    "oidcToken": {
                        "serviceAccountEmail": "scheduler-sa@example.test",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/usr/bin/env bash\n'
        'printf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n'
        'if [[ "$*" == *"scheduler jobs describe"* && "$*" == *"--format=json"* ]]; then\n'
        f'  cat "{snapshot}"\n'
        'fi\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'restore_scheduler_trigger "paused-trigger" "{snapshot}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_SCHEDULER_HELPER": str(SCHEDULER_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    call = gcloud_log.read_text(encoding="utf-8")
    assert "scheduler jobs pause paused-trigger" in call


def test_scheduler_trigger_restore_deletes_absent_pre_deploy_trigger(tmp_path: Path) -> None:
    snapshot = tmp_path / "scheduler.json"
    snapshot.write_text('{"exists": false}\n', encoding="utf-8")
    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'restore_scheduler_trigger "absent-trigger" "{snapshot}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_SCHEDULER_HELPER": str(SCHEDULER_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    call = gcloud_log.read_text(encoding="utf-8")
    assert "scheduler jobs delete absent-trigger" in call


def test_scheduler_trigger_restore_partial_failure_continues_and_reports_diagnostics(
    tmp_path: Path,
) -> None:
    snap1 = tmp_path / "snap1.json"
    snap1.write_text(
        json.dumps(
            {
                "schedule": "0 * * * *",
                "timeZone": "UTC",
                "httpTarget": {
                    "uri": "https://example.test/1",
                    "oidcToken": {"serviceAccountEmail": "sa1@example.test"},
                },
            }
        ),
        encoding="utf-8",
    )
    snap2 = tmp_path / "snap2.json"
    snap2.write_text(
        json.dumps(
            {
                "schedule": "0 * * * *",
                "timeZone": "UTC",
                "httpTarget": {
                    "uri": "https://example.test/2",
                    "oidcToken": {"serviceAccountEmail": "sa2@example.test"},
                },
            }
        ),
        encoding="utf-8",
    )
    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$*" == *"trigger1"* && "$*" == *"update"* ]]; then\n'
        '  echo "Simulated error on trigger1" >&2\n'
        '  exit 1\n'
        'fi\n'
        'printf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n'
        'if [[ "$*" == *"scheduler jobs describe"* && "$*" == *"--format=json"* ]]; then\n'
        '  if [[ "$*" == *"trigger2"* ]]; then\n'
        f'    cat "{snap2}"\n'
        '  else\n'
        f'    cat "{snap1}"\n'
        '  fi\n'
        'fi\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'rollback_status=0\n'
        f'restore_scheduler_trigger "trigger1" "{snap1}" || rollback_status=$?\n'
        f'restore_scheduler_trigger "trigger2" "{snap2}" || rollback_status=$?\n'
        f'exit "${{rollback_status}}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_SCHEDULER_HELPER": str(SCHEDULER_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Error: failed to update Cloud Scheduler trigger 'trigger1'." in result.stderr
    call = gcloud_log.read_text(encoding="utf-8")
    assert "trigger2" in call


def test_scheduler_trigger_restore_fails_closed_when_readback_describe_fails(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "scheduler.json"
    snapshot.write_text(
        json.dumps(
            {
                "schedule": "0 * * * *",
                "timeZone": "Asia/Taipei",
                "httpTarget": {
                    "uri": "https://example.test/job:run",
                    "oidcToken": {"serviceAccountEmail": "sa@example.test"},
                },
            }
        ),
        encoding="utf-8",
    )
    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$*" == *"scheduler jobs describe"* && "$*" == *"--format=json"* ]]; then\n'
        '  echo "gcloud describe error" >&2\n'
        '  exit 1\n'
        'fi\n'
        'printf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'restore_scheduler_trigger "worker-trigger" "{snapshot}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_SCHEDULER_HELPER": str(SCHEDULER_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Error: failed to capture or validate readback snapshot for Cloud Scheduler trigger 'worker-trigger'." in result.stderr


def test_scheduler_trigger_restore_fails_closed_when_readback_drift_detected(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "scheduler.json"
    snapshot.write_text(
        json.dumps(
            {
                "schedule": "0 * * * *",
                "timeZone": "Asia/Taipei",
                "httpTarget": {
                    "uri": "https://example.test/job:run",
                    "oidcToken": {"serviceAccountEmail": "sa@example.test"},
                },
            }
        ),
        encoding="utf-8",
    )
    drifted_snapshot = tmp_path / "drifted.json"
    drifted_snapshot.write_text(
        json.dumps(
            {
                "schedule": "0 * * * *",
                "timeZone": "UTC",  # drifted timeZone
                "httpTarget": {
                    "uri": "https://example.test/job:run",
                    "oidcToken": {"serviceAccountEmail": "sa@example.test"},
                },
            }
        ),
        encoding="utf-8",
    )
    gcloud_log = tmp_path / "gcloud.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$*" == *"scheduler jobs describe"* && "$*" == *"--format=json"* ]]; then\n'
        f'  cat "{drifted_snapshot}"\n'
        '  exit 0\n'
        'fi\n'
        'printf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n',
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    command = (
        f'source "{TRAFFIC_SHELL_HELPER}"\n'
        f'restore_scheduler_trigger "worker-trigger" "{snapshot}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(gcloud_log),
            "GCP_PROJECT": "test-project",
            "GCP_REGION": "test-region",
            "ODP_SCHEDULER_HELPER": str(SCHEDULER_HELPER_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Error: trigger 'worker-trigger' readback configuration drift detected." in result.stderr



def test_scheduler_trigger_compare_verifies_redacted_equality_and_detects_drift() -> None:
    spec = importlib.util.spec_from_file_location("cloud_scheduler_trigger", SCHEDULER_HELPER_PATH)
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    before = {
        "userUpdateTime": "2026-08-01T10:00:00Z",
        "schedule": "0 * * * *",
        "timeZone": "Asia/Taipei",
        "state": "ENABLED",
        "httpTarget": {
            "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/j:run",
            "httpMethod": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": "e30=",
            "oidcToken": {
                "serviceAccountEmail": "sa@example.test",
                "audience": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/j:run",
            },
        },
    }
    after_same = {
        "userUpdateTime": "2026-08-02T15:20:00Z",
        "schedule": "0 * * * *",
        "timeZone": "Asia/Taipei",
        "state": "ENABLED",
        "httpTarget": {
            "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/j:run",
            "httpMethod": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": "e30=",
            "oidcToken": {
                "serviceAccountEmail": "sa@example.test",
                "audience": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/j:run",
            },
        },
    }
    after_drift = {
        "userUpdateTime": "2026-08-02T15:20:00Z",
        "schedule": "0 * * * *",
        "timeZone": "Asia/Taipei",
        "state": "ENABLED",
        "httpTarget": {
            "uri": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/DIFFERENT-job:run",
            "httpMethod": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": "e30=",
            "oidcToken": {
                "serviceAccountEmail": "sa@example.test",
                "audience": "https://run.googleapis.com/v2/projects/p/locations/r/jobs/j:run",
            },
        },
    }

    assert helper.compare_snapshots(before, after_same) is True
    assert helper.compare_snapshots(before, after_drift) is False


def test_web_image_carries_release_and_live_binding_metadata() -> None:
    dockerfile = (ROOT / "infra/docker/web.Dockerfile").read_text(encoding="utf-8")


    for token in (
        "ARG ODAY_RELEASE_SHA",
        "ARG ODP_REQUIRE_LIVE_DATA",
        "ARG ODP_DATA_BINDING_MODE",
        "ARG ODP_PRODUCT_MODE",
        "NEXT_PUBLIC_ODAY_RELEASE_SHA",
        "NEXT_PUBLIC_ODP_DATA_BINDING_MODE",
        "NEXT_PUBLIC_ODP_PRODUCT_MODE",
    ):
        assert token in dockerfile


def test_worker_and_scheduler_images_use_bounded_job_entrypoint() -> None:
    worker = (ROOT / "infra/docker/worker.Dockerfile").read_text(encoding="utf-8")
    scheduler = (ROOT / "infra/docker/scheduler.Dockerfile").read_text(encoding="utf-8")

    for dockerfile in (worker, scheduler):
        assert (
            'ENTRYPOINT ["python", "product_ops/deployment/cloud_run_job_entrypoint.py"]'
            in dockerfile
        )
    assert 'CMD ["worker", "--max-jobs", "100"]' in worker
    assert 'CMD ["scheduler"]' in scheduler


def test_docker_python_runtime_images_install_from_frozen_uv_lock() -> None:
    """Contract: API, worker, scheduler runtime images only install from uv.lock frozen resolution."""
    live_dockerfiles = {
        "api": ROOT / "infra/docker/api.Dockerfile",
        "worker": ROOT / "infra/docker/worker.Dockerfile",
        "scheduler": ROOT / "infra/docker/scheduler.Dockerfile",
    }

    # Verify canonical three live Python Dockerfiles exist
    for role, path in live_dockerfiles.items():
        assert path.is_file(), f"Missing live Dockerfile for {role}: {path}"
        content = path.read_text(encoding="utf-8")

        # Must copy uv from an immutable digest, rejecting mutable :latest
        assert "COPY --from=ghcr.io/astral-sh/uv" in content, (
            f"{role}.Dockerfile must copy uv binary from ghcr.io/astral-sh/uv"
        )
        assert ":latest" not in content, (
            f"{role}.Dockerfile must not use mutable :latest tag for uv or base images"
        )
        assert "@sha256:" in content, (
            f"{role}.Dockerfile must pin uv to an explicit immutable digest"
        )

        # Must copy both pyproject.toml and uv.lock
        assert "COPY pyproject.toml uv.lock ./" in content, (
            f"{role}.Dockerfile must copy pyproject.toml and uv.lock"
        )

        # Must use frozen export from uv.lock
        assert "uv export --frozen --no-dev --no-emit-project" in content, (
            f"{role}.Dockerfile must export dependencies with --frozen from uv.lock"
        )

        # Must use canonical locked install with require-hashes
        assert (
            "uv pip install --no-cache --system --require-hashes -r" in content
        ), (
            f"{role}.Dockerfile must install via canonical 'uv pip install --no-cache --system --require-hashes -r'"
        )

        # Must clean up temporary requirements file
        assert "rm -f /tmp/requirements.txt" in content, (
            f"{role}.Dockerfile must clean up temporary /tmp/requirements.txt"
        )

        # Must not parse pyproject.toml at build time directly or hand-pin individual packages
        assert "tomllib.load" not in content, (
            f"{role}.Dockerfile must not parse pyproject.toml dependencies dynamically"
        )
        assert '"alembic>=' not in content, (
            f"{role}.Dockerfile must not hand-pin alembic; uv.lock is authority"
        )
        assert '"psycopg[binary,pool]>=' not in content, (
            f"{role}.Dockerfile must not hand-pin psycopg; uv.lock is authority"
        )

    # No parallel requirements files maintained in infra/docker
    docker_dir = ROOT / "infra/docker"
    parallel_reqs = list(docker_dir.glob("*requirements*.txt"))
    assert not parallel_reqs, f"Found parallel requirements files: {parallel_reqs}"


# Deploy Dev run 30376737123 selected exactly these three providers, so
# listing.partner_feed was never deployed and its API key was never bound.
RUN_30376737123_SHA = "dda726155a399487474ae148b4dc1c3294ea9463"
RUN_30376737123_PROVIDER_IDS = (
    "poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset"
)
SELECTED_PROVIDER_SECRET_ENVS = (
    "ODP_POI_PROVIDER_API_KEY",
    "ODP_GEOCODE_PROVIDER_API_KEY",
    "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN",
)


def _knative_secret_env(name: str, secret: str | None = None) -> dict[str, object]:
    """Env entry in the Knative schema gcloud emits for `--set-secrets`."""

    return {
        "name": name,
        "valueFrom": {
            "secretKeyRef": {"name": secret or name.lower().replace("_", "-"), "key": "latest"}
        },
    }


def _v2_secret_env(name: str, secret: str | None = None) -> dict[str, object]:
    """Env entry in the Cloud Run v2 schema."""

    return {
        "name": name,
        "valueSource": {
            "secretKeyRef": {
                "secret": secret or name.lower().replace("_", "-"),
                "version": "latest",
            }
        },
    }


def _job_container(
    *,
    kind: str,
    sha: str,
    provider_ids: str | None,
    secret_envs: tuple[dict[str, object], ...],
    extra_envs: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    mode = "migrate" if kind == "migration" else kind
    env: list[dict[str, object]] = [{"name": "ODAY_RELEASE_SHA", "value": sha}]
    if provider_ids is not None:
        env.append({"name": "ODP_PRODUCTION_PROVIDER_IDS", "value": provider_ids})
    env.extend(secret_envs)
    env.extend(extra_envs)
    return {
        "image": f"registry/{kind}:dev-{sha}",
        "command": ["python"],
        "args": ["product_ops/deployment/cloud_run_job_entrypoint.py", mode],
        "env": env,
    }


def _knative_job(
    *,
    kind: str = "migration",
    name: str = "oday-migration-r-dda726155a39",
    sha: str = RUN_30376737123_SHA,
    provider_ids: str | None = RUN_30376737123_PROVIDER_IDS,
    secret_envs: tuple[dict[str, object], ...] | None = None,
    extra_envs: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    """Job description in the schema `gcloud run jobs describe` emits (v1)."""

    if secret_envs is None:
        secret_envs = tuple(
            _knative_secret_env(env_var)
            for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        )
    container = _job_container(
        kind=kind,
        sha=sha,
        provider_ids=provider_ids,
        secret_envs=secret_envs,
        extra_envs=extra_envs,
    )
    return {
        "metadata": {
            "name": name,
            "labels": {"oday-release-sha": sha, "oday-runtime": kind},
        },
        "spec": {
            "template": {"spec": {"template": {"spec": {"containers": [container]}}}},
        },
    }


def _v2_job(
    *,
    kind: str = "worker",
    sha: str = RUN_30376737123_SHA,
    provider_ids: str | None = RUN_30376737123_PROVIDER_IDS,
) -> dict[str, object]:
    container = _job_container(
        kind=kind,
        sha=sha,
        provider_ids=provider_ids,
        secret_envs=tuple(
            _v2_secret_env(env_var)
            for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        ),
    )
    return {
        "name": f"projects/oday/locations/asia-east1/jobs/oday-{kind}",
        "labels": {"oday-release-sha": sha},
        "template": {"template": {"containers": [container]}},
    }


def _v2_job_with_envs(
    *,
    provider_ids: str | None = RUN_30376737123_PROVIDER_IDS,
    secret_envs: tuple[dict[str, object], ...] | None = None,
    extra_envs: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    """A v2 job description with the env knobs `_knative_job` exposes.

    Lets a Knative regression be mirrored onto the Cloud Run v2 container path
    without restating the description shape in every test.
    """

    if secret_envs is None:
        secret_envs = tuple(
            _v2_secret_env(env_var)
            for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        )
    job = _v2_job(provider_ids=provider_ids)
    job["template"] = {
        "template": {
            "containers": [
                _job_container(
                    kind="worker",
                    sha=RUN_30376737123_SHA,
                    provider_ids=provider_ids,
                    secret_envs=secret_envs,
                    extra_envs=extra_envs,
                )
            ]
        }
    }
    return job


def _succeeded_execution(name: str = "oday-migration-r-dda726155a39-ndb4l") -> dict[str, object]:
    return {
        "metadata": {"name": name},
        "status": {
            "succeededCount": 1,
            "failedCount": 0,
            "completionTime": "2026-07-28T16:00:00Z",
            "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}],
        },
    }


def _job_checks(job: dict[str, object], *, kind: str = "migration", **kwargs: object):
    return validator.cloud_run_job_checks(
        kind=kind,
        job_description=job,
        execution=_succeeded_execution(),
        expected_sha=kwargs.pop("expected_sha", RUN_30376737123_SHA),
        **kwargs,
    )


def _failed_names(checks) -> set[str]:
    return {check.name for check in checks if not check.ok}


def _detail(checks, name: str) -> str:
    return next(check.detail for check in checks if check.name == name)


def test_job_smoke_reproduces_run_30376737123_secret_binding_failure() -> None:
    """The exact migration receipt from run 30376737123 must now pass.

    `oday-migration-r-dda726155a39` executed successfully as
    `...-ndb4l`, but `jobs-smoke:migration:secret_bindings` failed because the
    old rule looked for `odp_listing_provider_api_key` anywhere in the job JSON
    while the release selected only three providers, none of them
    `listing.partner_feed`.
    """

    job = _knative_job()

    # The receipt genuinely has no listing key: that is what the old substring
    # rule tripped on, and it is not a deployment defect.
    assert "odp_listing_provider_api_key" not in json.dumps(job).lower()

    checks, report = _job_checks(job)

    assert all(check.ok for check in checks), _failed_names(checks)
    assert report["selected_provider_ids"] == sorted(RUN_30376737123_PROVIDER_IDS.split(","))
    assert "ODP_LISTING_PROVIDER_API_KEY" not in report["required_secret_env_vars"]
    assert report["required_secret_env_vars"] == sorted(
        ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
    )
    assert report["job_name"] == "oday-migration-r-dda726155a39"
    assert report["execution_name"] == "oday-migration-r-dda726155a39-ndb4l"
    assert report["secret_values_redacted"] is True


def test_job_smoke_requires_listing_secret_only_when_listing_is_selected() -> None:
    selected = f"{RUN_30376737123_PROVIDER_IDS},listing.partner_feed"

    missing_checks, missing_report = _job_checks(_knative_job(provider_ids=selected))

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(missing_checks)
    assert "ODP_LISTING_PROVIDER_API_KEY" in _detail(
        missing_checks, "jobs-smoke:migration:secret_bindings"
    )
    assert "ODP_LISTING_PROVIDER_API_KEY" in missing_report["required_secret_env_vars"]

    bound_checks, bound_report = _job_checks(
        _knative_job(
            provider_ids=selected,
            secret_envs=tuple(
                _knative_secret_env(env_var)
                for env_var in (
                    "ODAY_DATABASE_URL",
                    "ODP_LISTING_PROVIDER_API_KEY",
                    *SELECTED_PROVIDER_SECRET_ENVS,
                )
            ),
        )
    )

    assert all(check.ok for check in bound_checks), _failed_names(bound_checks)
    assert "ODP_LISTING_PROVIDER_API_KEY" in bound_report["secret_bound_env_vars"]


def test_job_smoke_requires_database_secret_for_every_selection() -> None:
    job = _knative_job(
        secret_envs=tuple(_knative_secret_env(env_var) for env_var in SELECTED_PROVIDER_SECRET_ENVS)
    )

    checks, report = _job_checks(job)

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODAY_DATABASE_URL" in _detail(checks, "jobs-smoke:migration:secret_bindings")
    assert "ODAY_DATABASE_URL" not in report["secret_bound_env_vars"]


def test_job_smoke_requires_every_selected_provider_secret() -> None:
    for dropped in SELECTED_PROVIDER_SECRET_ENVS:
        job = _knative_job(
            secret_envs=tuple(
                _knative_secret_env(env_var)
                for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
                if env_var != dropped
            )
        )

        checks, _ = _job_checks(job)

        assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
        assert dropped in _detail(checks, "jobs-smoke:migration:secret_bindings")


def test_job_smoke_rejects_plaintext_provider_secret() -> None:
    plaintext = "sk-live-real-poi-key"
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            {"name": "ODP_POI_PROVIDER_API_KEY", "value": plaintext},
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "plaintext" in detail
    assert plaintext not in detail
    assert plaintext not in json.dumps(report)
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]


@pytest.mark.parametrize(
    "malformed",
    [
        {"name": "ODP_POI_PROVIDER_API_KEY"},
        {"name": "ODP_POI_PROVIDER_API_KEY", "valueFrom": {}},
        {"name": "ODP_POI_PROVIDER_API_KEY", "valueSource": {}},
        {"name": "ODP_POI_PROVIDER_API_KEY", "valueFrom": {"secretKeyRef": {}}},
        {"name": "ODP_POI_PROVIDER_API_KEY", "valueFrom": {"secretKeyRef": {"name": ""}}},
        {
            "name": "ODP_POI_PROVIDER_API_KEY",
            "valueFrom": {"secretKeyRef": {"name": "placeholder", "key": "latest"}},
        },
        # A `secretKeyRef` hoisted to the top level is not a schema Cloud Run
        # emits in either API version.
        {
            "name": "ODP_POI_PROVIDER_API_KEY",
            "secretKeyRef": {"name": "odp-poi-provider-api-key", "key": "latest"},
        },
        # The v2 reference key inside the Knative env source: neither schema.
        {
            "name": "ODP_POI_PROVIDER_API_KEY",
            "valueFrom": {"secretKeyRef": {"secret": "odp-poi-provider-api-key"}},
        },
        # The Knative reference key inside the v2 env source: neither schema.
        {
            "name": "ODP_POI_PROVIDER_API_KEY",
            "valueSource": {"secretKeyRef": {"name": "odp-poi-provider-api-key"}},
        },
    ],
)
def test_job_smoke_rejects_malformed_secret_binding(malformed: dict[str, object]) -> None:
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            malformed,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, _ = _job_checks(job)

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in _detail(checks, "jobs-smoke:migration:secret_bindings")


def test_job_smoke_rejects_a_knative_job_whose_secrets_use_the_v2_schema() -> None:
    """The container path fixes the dialect; a whole-description crossover fails.

    Accepting either secret schema regardless of where the containers were
    found meant a Knative-path job could bind every required secret in the
    Cloud Run v2 dialect and still pass. `gcloud` never emits that shape, so it
    proves nothing about Secret Manager and must fail closed.
    """

    crossed = _knative_job(
        secret_envs=tuple(
            _v2_secret_env(env_var)
            for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        )
    )

    checks, report = _job_checks(crossed)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS):
        assert env_var in detail
    assert "valueFrom.secretKeyRef.name" in detail
    assert report["secret_bound_env_vars"] == []

    # The selection itself is still readable, so only the bindings check fails:
    # this is a binding-schema defect, not an unreadable task template.
    assert "jobs-smoke:migration:provider_selection" not in _failed_names(checks)


def test_job_smoke_rejects_a_v2_job_whose_secrets_use_the_knative_schema() -> None:
    """The mirror crossover: v2 container path, Knative `valueFrom` bindings."""

    container = _job_container(
        kind="worker",
        sha=RUN_30376737123_SHA,
        provider_ids=RUN_30376737123_PROVIDER_IDS,
        secret_envs=tuple(
            _knative_secret_env(env_var)
            for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        ),
    )
    crossed = _v2_job()
    crossed["template"] = {"template": {"containers": [container]}}

    checks, report = _job_checks(crossed, kind="worker")
    detail = _detail(checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(checks)
    for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS):
        assert env_var in detail
    assert "valueSource.secretKeyRef.secret" in detail
    assert report["secret_bound_env_vars"] == []
    assert "jobs-smoke:worker:provider_selection" not in _failed_names(checks)


@pytest.mark.parametrize(
    "duplicate",
    [
        # The exploit: two well-formed bindings naming different secrets. Both
        # entries validate individually, and nothing says which one the runtime
        # reads, so the proof names a secret the task may never receive.
        _knative_secret_env("ODP_POI_PROVIDER_API_KEY", "attacker-controlled-secret"),
        # An identical repeat is ambiguous for the same reason the duplicate
        # selection is: a repeated env var has no defined winner.
        _knative_secret_env("ODP_POI_PROVIDER_API_KEY"),
        # A plaintext occurrence beside the secret-bound one.
        {"name": "ODP_POI_PROVIDER_API_KEY", "value": "sk-live-real-poi-key"},
        # An off-schema second occurrence is still a second occurrence.
        {"name": "ODP_POI_PROVIDER_API_KEY", "valueFrom": {"secretKeyRef": {}}},
    ],
)
def test_job_smoke_rejects_a_duplicate_required_secret_binding(
    duplicate: dict[str, object],
) -> None:
    """A required env may be bound exactly once, whatever the second entry says."""

    job = _knative_job(extra_envs=(duplicate,))

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert "ambiguous" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]
    assert "sk-live-real-poi-key" not in json.dumps(report)
    assert "sk-live-real-poi-key" not in detail

    # The selection is untouched, so this is a binding defect only.
    assert "jobs-smoke:migration:provider_selection" not in _failed_names(checks)


@pytest.mark.parametrize(
    ("mixed", "expected_location"),
    [
        # The exploit: a valid Knative binding beside a conflicting v2 one. The
        # reader took the first and ignored the second, so one description named
        # two different secrets depending on who read it.
        (
            {
                "name": "ODP_POI_PROVIDER_API_KEY",
                "valueFrom": {"secretKeyRef": {"name": "odp-poi-provider-api-key"}},
                "valueSource": {"secretKeyRef": {"secret": "attacker-controlled-secret"}},
            },
            "valueSource",
        ),
        # A `secretKeyRef` hoisted to the top level beside a valid binding.
        (
            {
                "name": "ODP_POI_PROVIDER_API_KEY",
                "valueFrom": {"secretKeyRef": {"name": "odp-poi-provider-api-key"}},
                "secretKeyRef": {"name": "attacker-controlled-secret"},
            },
            "secretKeyRef",
        ),
        # Both dialects' reference keys inside this dialect's own source.
        (
            {
                "name": "ODP_POI_PROVIDER_API_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "odp-poi-provider-api-key",
                        "secret": "attacker-controlled-secret",
                    }
                },
            },
            "valueFrom.secretKeyRef.secret",
        ),
        # An empty off-dialect source is still a source gcloud does not emit.
        (
            {
                "name": "ODP_POI_PROVIDER_API_KEY",
                "valueFrom": {"secretKeyRef": {"name": "odp-poi-provider-api-key"}},
                "valueSource": {},
            },
            "valueSource",
        ),
    ],
)
def test_job_smoke_rejects_an_entry_mixing_secret_binding_dialects(
    mixed: dict[str, object], expected_location: str
) -> None:
    """One env entry may declare one secret source, in this job's dialect only."""

    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            mixed,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert expected_location in detail
    assert "valueFrom.secretKeyRef.name" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]
    assert "attacker-controlled-secret" not in detail
    assert "attacker-controlled-secret" not in json.dumps(report)


def test_job_smoke_rejects_a_v2_entry_mixing_secret_binding_dialects() -> None:
    """The mirror: a v2 job whose binding also carries the Knative source."""

    container = _job_container(
        kind="worker",
        sha=RUN_30376737123_SHA,
        provider_ids=RUN_30376737123_PROVIDER_IDS,
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            {
                "name": "ODP_POI_PROVIDER_API_KEY",
                "valueSource": {"secretKeyRef": {"secret": "odp-poi-provider-api-key"}},
                "valueFrom": {"secretKeyRef": {"name": "attacker-controlled-secret"}},
            },
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        ),
    )
    mixed = _v2_job()
    mixed["template"] = {"template": {"containers": [container]}}

    checks, report = _job_checks(mixed, kind="worker")
    detail = _detail(checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert "valueFrom" in detail
    assert "valueSource.secretKeyRef.secret" in detail
    assert "attacker-controlled-secret" not in json.dumps(report)


def test_job_smoke_rejects_a_selection_entry_carrying_an_off_dialect_secret() -> None:
    """A plaintext selection is unreadable once the entry also binds a secret.

    Reading the plaintext and ignoring the off-dialect binding beside it would
    validate against a value the runtime may never see.
    """

    job = _knative_job(
        provider_ids=None,
        extra_envs=(
            {
                "name": "ODP_PRODUCTION_PROVIDER_IDS",
                "value": RUN_30376737123_PROVIDER_IDS,
                "valueSource": {"secretKeyRef": {"secret": "odp-production-provider-ids"}},
            },
        ),
    )

    checks, report = _job_checks(job)

    assert "jobs-smoke:migration:provider_selection" in _failed_names(checks)
    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert report["selected_provider_ids"] == []


#: Literal payloads that are falsy or not a string. Each one sat beside a valid
#: `secretKeyRef` and produced zero failing checks while the proof tested
#: `isinstance(value, str) and value.strip()` instead of the key's presence.
_BLANK_LITERAL_VALUES: tuple[object, ...] = ("", "   ", "\t\n", 0, False, [], {}, None)


@pytest.mark.parametrize("literal", _BLANK_LITERAL_VALUES)
def test_job_smoke_rejects_a_secret_binding_carrying_a_literal_value_key(
    literal: object,
) -> None:
    """A valid secret reference plus a blank literal is still two sources of truth.

    `gcloud run jobs describe` emits `value` or a secret source for an env
    entry, never both, so a `value` key beside a `secretKeyRef` means the env
    var resolves differently for any reader that prefers the literal. Rejecting
    only truthy strings left every falsy and non-string payload passing.
    Both dialects are pinned: the exploit is in the shared literal rule, not in
    one API version's schema.
    """

    knative_entry = dict(_knative_secret_env("ODP_POI_PROVIDER_API_KEY"))
    knative_entry["value"] = literal
    knative = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            knative_entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    knative_checks, knative_report = _job_checks(knative)
    knative_detail = _detail(knative_checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(knative_checks)
    assert "ODP_POI_PROVIDER_API_KEY" in knative_detail
    assert "literal value key" in knative_detail
    assert "ODP_POI_PROVIDER_API_KEY" not in knative_report["secret_bound_env_vars"]

    v2_entry = dict(_v2_secret_env("ODP_POI_PROVIDER_API_KEY"))
    v2_entry["value"] = literal
    v2 = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            v2_entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    v2_checks, v2_report = _job_checks(v2, kind="worker")
    v2_detail = _detail(v2_checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(v2_checks)
    assert "ODP_POI_PROVIDER_API_KEY" in v2_detail
    assert "literal value key" in v2_detail
    assert "ODP_POI_PROVIDER_API_KEY" not in v2_report["secret_bound_env_vars"]


#: Secret sources written in the job's *own* dialect that declare a binding
#: without resolving to one. None of them carries the other dialect's keys, so
#: `_foreign_secret_binding_locations` never saw them and the entry read as
#: pure plaintext.
_EMPTY_SAME_DIALECT_SOURCES: tuple[dict[str, object], ...] = (
    {},
    {"secretKeyRef": {}},
    {"secretKeyRef": {"key": "latest"}},
)


@pytest.mark.parametrize("empty_source", _EMPTY_SAME_DIALECT_SOURCES)
def test_job_smoke_rejects_a_selection_entry_declaring_an_empty_same_dialect_source(
    empty_source: dict[str, object],
) -> None:
    """A selection entry that also declares a secret source is not readable plaintext.

    Round 6 rejected an *off-dialect* source beside the plaintext selection but
    read the literal happily when the source was this job's own dialect and
    merely empty. The runtime resolves that entry from Secret Manager, so the
    plaintext the gate validated against is not what the job receives.
    """

    knative = _knative_job(
        provider_ids=None,
        extra_envs=(
            {
                "name": "ODP_PRODUCTION_PROVIDER_IDS",
                "value": RUN_30376737123_PROVIDER_IDS,
                "valueFrom": empty_source,
            },
        ),
    )

    knative_checks, knative_report = _job_checks(knative)
    knative_failed = _failed_names(knative_checks)

    assert "jobs-smoke:migration:provider_selection" in knative_failed
    assert "jobs-smoke:migration:secret_bindings" in knative_failed
    assert "valueFrom" in _detail(knative_checks, "jobs-smoke:migration:provider_selection")
    assert knative_report["selected_provider_ids"] == []

    v2 = _v2_job_with_envs(
        provider_ids=None,
        extra_envs=(
            {
                "name": "ODP_PRODUCTION_PROVIDER_IDS",
                "value": RUN_30376737123_PROVIDER_IDS,
                "valueSource": empty_source,
            },
        ),
    )

    v2_checks, v2_report = _job_checks(v2, kind="worker")
    v2_failed = _failed_names(v2_checks)

    assert "jobs-smoke:worker:provider_selection" in v2_failed
    assert "jobs-smoke:worker:secret_bindings" in v2_failed
    assert "valueSource" in _detail(v2_checks, "jobs-smoke:worker:provider_selection")
    assert v2_report["selected_provider_ids"] == []


#: Env-source members that sat inside the *accepted* dialect's source beside a
#: valid `secretKeyRef` and produced zero failing checks. `configMapKeyRef` is
#: the load-bearing case: Knative defines it, Cloud Run does not support it, and
#: it names a second value for the same env var. The last entry is a member
#: planted inside `secretKeyRef` itself, which the round-6 cross-dialect rule
#: never looked for because it is not the other dialect's key.
_UNSUPPORTED_KNATIVE_SOURCE_MEMBERS: tuple[tuple[dict[str, object], str], ...] = (
    (
        {"configMapKeyRef": {"name": "attacker-controlled-config", "key": "latest"}},
        "valueFrom.configMapKeyRef",
    ),
    ({"fieldRef": {"fieldPath": "metadata.name"}}, "valueFrom.fieldRef"),
    (
        {"resourceFieldRef": {"resource": "limits.memory"}},
        "valueFrom.resourceFieldRef",
    ),
)

_UNSUPPORTED_V2_SOURCE_MEMBERS: tuple[tuple[dict[str, object], str], ...] = (
    (
        {"configMapKeyRef": {"secret": "attacker-controlled-config", "version": "latest"}},
        "valueSource.configMapKeyRef",
    ),
    ({"fieldRef": {"fieldPath": "metadata.name"}}, "valueSource.fieldRef"),
)


@pytest.mark.parametrize(
    ("extra_member", "location"),
    _UNSUPPORTED_KNATIVE_SOURCE_MEMBERS,
)
def test_job_smoke_rejects_a_knative_source_member_beside_secret_key_ref(
    extra_member: dict[str, object], location: str
) -> None:
    """A second source *inside* the accepted dialect is still a second source.

    Rounds 6 and 7 made the entry carry exactly one source but never looked
    inside it. A required secret entry with a valid `valueFrom.secretKeyRef`
    plus `valueFrom.configMapKeyRef` therefore returned zero failing checks,
    although Cloud Run v1 does not support `configMapKeyRef` and the entry names
    two values for one env var.
    """

    entry = dict(_knative_secret_env("ODP_POI_PROVIDER_API_KEY"))
    entry["valueFrom"] = {**entry["valueFrom"], **extra_member}
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert location in detail
    assert "valueFrom.secretKeyRef.name" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]
    assert "attacker-controlled-config" not in json.dumps(report)


@pytest.mark.parametrize(
    ("extra_member", "location"),
    _UNSUPPORTED_V2_SOURCE_MEMBERS,
)
def test_job_smoke_rejects_a_v2_source_member_beside_secret_key_ref(
    extra_member: dict[str, object], location: str
) -> None:
    """The v2 mirror of the same fail-open, pinned on its own container path."""

    entry = dict(_v2_secret_env("ODP_POI_PROVIDER_API_KEY"))
    entry["valueSource"] = {**entry["valueSource"], **extra_member}
    job = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job, kind="worker")
    detail = _detail(checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert location in detail
    assert "valueSource.secretKeyRef.secret" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]
    assert "attacker-controlled-config" not in json.dumps(report)


def test_job_smoke_rejects_a_member_planted_inside_the_secret_key_ref() -> None:
    """A reference carries only the fields its own dialect defines.

    Knative's `SecretKeySelector` is `name`/`key`/`optional`; Cloud Run v2's is
    `secret`/`version`. A `value` planted beside a valid reference is not the
    other dialect's key, so the round-6 cross-dialect rule never saw it.
    """

    knative_entry = dict(_knative_secret_env("ODP_POI_PROVIDER_API_KEY"))
    knative_entry["valueFrom"] = {
        "secretKeyRef": {
            **knative_entry["valueFrom"]["secretKeyRef"],
            "value": "sk-live-plaintext",
        }
    }
    knative = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            knative_entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    knative_checks, knative_report = _job_checks(knative)
    knative_detail = _detail(knative_checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(knative_checks)
    assert "valueFrom.secretKeyRef.value" in knative_detail
    assert "sk-live-plaintext" not in knative_detail
    assert "sk-live-plaintext" not in json.dumps(knative_report)

    v2_entry = dict(_v2_secret_env("ODP_POI_PROVIDER_API_KEY"))
    v2_entry["valueSource"] = {
        "secretKeyRef": {**v2_entry["valueSource"]["secretKeyRef"], "key": "latest"}
    }
    v2 = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            v2_entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    v2_checks, _ = _job_checks(v2, kind="worker")
    v2_detail = _detail(v2_checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(v2_checks)
    assert "valueSource.secretKeyRef.key" in v2_detail


def test_job_smoke_accepts_the_optional_members_each_dialect_defines() -> None:
    """The member rule is an allowlist of real API fields, not a two-key rule.

    Knative's selector may carry `optional`; a job that uses it still binds a
    secret, so the tightening must not fail a shape the API defines.
    """

    entry = dict(_knative_secret_env("ODP_POI_PROVIDER_API_KEY"))
    entry["valueFrom"] = {"secretKeyRef": {**entry["valueFrom"]["secretKeyRef"], "optional": False}}
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, _ = _job_checks(job)

    assert all(check.ok for check in checks), _failed_names(checks)


#: The secret every one of these selectors claims to bind.
_POI_SECRET = "odp-poi-provider-api-key"

#: Secret Manager caps a version alias at 63 characters. 255 is the cap on a
#: secret *name*, a different resource; round 9 validated the alias against that
#: number, so every length between 64 and 255 was accepted. The 63-character
#: alias is the boundary control that keeps the correction from over-tightening.
_ALIAS_63 = "a" + "b" * 62
_ALIAS_64 = "a" + "b" * 63
_ALIAS_255 = "a" + "b" * 254

#: Secret Manager caps a secret *ID* at 255 characters, so 255 is the boundary
#: control on the name member and 256 is the first length that names nothing.
_SECRET_ID_255 = "a" + "b" * 254
_SECRET_ID_256 = "a" + "b" * 255

#: Round 13: a Secret Manager version number and a Cloud Resource Manager
#: project number are int64 resource components, and rounds 10 and 11 checked
#: only their lexical shape. `9223372036854775808` is the first decimal past the
#: signed int64 maximum, so it named a version Secret Manager cannot hold and a
#: project number that service never issued while the mandatory binding around
#: it reported zero failed checks. The 30-digit form is the same defect written
#: long enough that no int64 reader can accept it either. `9223372036854775807`
#: is the boundary control that keeps the range check from over-tightening onto
#: the last number each component may really carry.
_INT64_MAX = str(2**63 - 1)
_OVER_INT64 = str(2**63)
_LONG_OVER_INT64 = "1" + "0" * 29

#: The two forms a Cloud Run secret binding may name a secret in. Both are kept
#: accepted because `product_ops/deployment/deploy_cloud_run_waji.sh` takes every name from an
#: operator-supplied `*_SECRET` variable, so a cross-project secret is a
#: deployment this proof must not fail.
_CROSS_PROJECT_SECRET = f"projects/oday-plus-prod/secrets/{_POI_SECRET}"
_CROSS_PROJECT_NUMBER_SECRET = f"projects/123456789012/secrets/{_POI_SECRET}"
_CROSS_PROJECT_INT64_MAX_SECRET = f"projects/{_INT64_MAX}/secrets/{_POI_SECRET}"
_CROSS_PROJECT_OVER_INT64_SECRET = f"projects/{_OVER_INT64}/secrets/{_POI_SECRET}"
_CROSS_PROJECT_LONG_OVER_INT64_SECRET = f"projects/{_LONG_OVER_INT64}/secrets/{_POI_SECRET}"

#: Round 11: names Secret Manager does not resolve. Round 10 fixed the version
#: member's grammar and stated the rule behind it — the description is the
#: proof, so the validator may not normalize what it checks — but the name
#: member beside it was still read through `.strip()` under no grammar at all,
#: so every one of these bound a "mandatory" secret to nothing while
#: `jobs-smoke:<kind>:secret_bindings` reported zero failed checks.
_UNUSABLE_SECRET_NAMES: tuple[object, ...] = (
    " odp-poi-provider-api-key ",
    " odp-poi-provider-api-key",
    "odp-poi-provider-api-key ",
    "\todp-poi-provider-api-key\n",
    "odp poi provider api key",
    "odp-poi-provider-api-key!",
    "odp.poi.provider.api.key",
    "odp/poi/provider/api/key",
    pytest.param(_SECRET_ID_256, id="secret-id-256-chars"),
    "資料庫",
    ".",
    # Path-shaped names that are still not the documented path: no project, no
    # secret ID, and a *version* path, which names a version rather than the
    # secret the binding must reference.
    f"projects//secrets/{_POI_SECRET}",
    "projects/oday-plus-prod/secrets/",
    f"projects/oday-plus-prod/secrets/{_POI_SECRET}/versions/1",
    # Round 13: a project *number* segment above the int64 range names a project
    # Cloud Resource Manager cannot have issued, so the secret behind it does
    # not resolve however well-formed the path around it looks.
    pytest.param(_CROSS_PROJECT_OVER_INT64_SECRET, id="cross-project-number-over-int64"),
    pytest.param(_CROSS_PROJECT_LONG_OVER_INT64_SECRET, id="cross-project-number-30-digits"),
    pytest.param(1, id="non-string-int"),
    pytest.param(None, id="non-string-none"),
)

#: The acceptance boundary the round-11 tightening must not cross: a bare secret
#: ID in either allowed character set, the 255-character maximum, and both
#: documented cross-project path spellings.
_USABLE_SECRET_NAMES: tuple[object, ...] = (
    _POI_SECRET,
    "odp_poi_provider_api_key",
    "OdpPoiProviderApiKey1",
    pytest.param(_SECRET_ID_255, id="secret-id-255-chars"),
    pytest.param(_CROSS_PROJECT_SECRET, id="cross-project-path"),
    pytest.param(_CROSS_PROJECT_NUMBER_SECRET, id="cross-project-number-path"),
    pytest.param(_CROSS_PROJECT_INT64_MAX_SECRET, id="cross-project-number-int64-max"),
)

#: Selectors whose member *names* all sit inside the dialect's allowlist while
#: the payloads cancel the binding they are supposed to prove. Round 8 closed
#: the reference to the members Knative defines but never read them, so each of
#: these returned zero failing checks:
#:
#: - `optional: true` tells Cloud Run the Secret or key need not exist, so the
#:   env var may simply be absent — the opposite of a mandatory binding;
#: - a non-boolean `optional` is not the field the API defines at all, so no
#:   reader can be assumed to treat it as `false`;
#: - Cloud Run v1 documents `key` as required, and a missing, blank, or
#:   unusable one selects no version, so the reference resolves to nothing;
#: - `localObjectReference` is Knative's superseded way of naming the secret
#:   `name` already names, so a selector holding both names two secrets.
_UNUSABLE_KNATIVE_SELECTORS: tuple[tuple[dict[str, object], str], ...] = (
    ({"name": _POI_SECRET, "key": "latest", "optional": True}, "optional"),
    ({"name": _POI_SECRET, "key": "latest", "optional": "true"}, "optional"),
    ({"name": _POI_SECRET, "key": "latest", "optional": "false"}, "optional"),
    ({"name": _POI_SECRET, "key": "latest", "optional": 1}, "optional"),
    ({"name": _POI_SECRET, "key": "latest", "optional": 0}, "optional"),
    ({"name": _POI_SECRET, "key": "latest", "optional": None}, "optional"),
    ({"name": _POI_SECRET}, "key"),
    ({"name": _POI_SECRET, "key": ""}, "key"),
    ({"name": _POI_SECRET, "key": "   "}, "key"),
    ({"name": _POI_SECRET, "key": "0"}, "key"),
    ({"name": _POI_SECRET, "key": "latest version"}, "key"),
    ({"name": _POI_SECRET, "key": "-1"}, "key"),
    ({"name": _POI_SECRET, "key": 1}, "key"),
    ({"name": _POI_SECRET, "key": None}, "key"),
    ({"name": _POI_SECRET, "key": "placeholder"}, "key"),
    # Round 10: the alias grammar itself. `latest` and `NEW` are reserved words
    # Secret Manager refuses as alias names in any case, only the exact
    # lowercase `latest` literal resolves, an alias is capped at 63 characters,
    # a version number is written canonically, and whitespace around a selector
    # is a defect in the description rather than something to normalize away.
    ({"name": _POI_SECRET, "key": "NEW"}, "key"),
    ({"name": _POI_SECRET, "key": "new"}, "key"),
    ({"name": _POI_SECRET, "key": "New"}, "key"),
    ({"name": _POI_SECRET, "key": "Latest"}, "key"),
    ({"name": _POI_SECRET, "key": "LATEST"}, "key"),
    ({"name": _POI_SECRET, "key": _ALIAS_64}, "key"),
    ({"name": _POI_SECRET, "key": _ALIAS_255}, "key"),
    ({"name": _POI_SECRET, "key": " latest "}, "key"),
    ({"name": _POI_SECRET, "key": " latest"}, "key"),
    ({"name": _POI_SECRET, "key": "latest "}, "key"),
    ({"name": _POI_SECRET, "key": "\tlatest\n"}, "key"),
    ({"name": _POI_SECRET, "key": " 1 "}, "key"),
    ({"name": _POI_SECRET, "key": "007"}, "key"),
    # Round 13: the range the version number carries. A canonical decimal above
    # the int64 maximum, at any length, pins a version Secret Manager cannot
    # hold, so it selects nothing however lexical the digits are.
    ({"name": _POI_SECRET, "key": _OVER_INT64}, "key"),
    ({"name": _POI_SECRET, "key": _LONG_OVER_INT64}, "key"),
    (
        {
            "name": _POI_SECRET,
            "key": "latest",
            "localObjectReference": {"name": "attacker-controlled-secret"},
        },
        "localObjectReference",
    ),
)

#: The v2 mirror. Cloud Run v2 documents `secret` as required and leaves
#: `version` optional, so only an unusable *declared* version is a defect here;
#: the absent-version control lives in
#: `test_job_smoke_accepts_a_v2_selector_without_a_declared_version`.
_UNUSABLE_V2_SELECTORS: tuple[tuple[dict[str, object], str], ...] = (
    ({"secret": _POI_SECRET, "version": ""}, "version"),
    ({"secret": _POI_SECRET, "version": "  "}, "version"),
    ({"secret": _POI_SECRET, "version": "0"}, "version"),
    ({"secret": _POI_SECRET, "version": "latest version"}, "version"),
    ({"secret": _POI_SECRET, "version": 1}, "version"),
    ({"secret": _POI_SECRET, "version": None}, "version"),
    # Round 10: the same alias grammar, on the v2 container path.
    ({"secret": _POI_SECRET, "version": "NEW"}, "version"),
    ({"secret": _POI_SECRET, "version": "new"}, "version"),
    ({"secret": _POI_SECRET, "version": "New"}, "version"),
    ({"secret": _POI_SECRET, "version": "Latest"}, "version"),
    ({"secret": _POI_SECRET, "version": "LATEST"}, "version"),
    ({"secret": _POI_SECRET, "version": _ALIAS_64}, "version"),
    ({"secret": _POI_SECRET, "version": _ALIAS_255}, "version"),
    ({"secret": _POI_SECRET, "version": " latest "}, "version"),
    ({"secret": _POI_SECRET, "version": " latest"}, "version"),
    ({"secret": _POI_SECRET, "version": "latest "}, "version"),
    ({"secret": _POI_SECRET, "version": "\tlatest\n"}, "version"),
    ({"secret": _POI_SECRET, "version": " 1 "}, "version"),
    ({"secret": _POI_SECRET, "version": "007"}, "version"),
    # Round 13: the int64 range is a Secret Manager fact, so the v2 `version`
    # member is bounded exactly as the Knative `key` member is.
    ({"secret": _POI_SECRET, "version": _OVER_INT64}, "version"),
    ({"secret": _POI_SECRET, "version": _LONG_OVER_INT64}, "version"),
)


@pytest.mark.parametrize(("selector", "member"), _UNUSABLE_KNATIVE_SELECTORS)
def test_job_smoke_rejects_an_unusable_knative_secret_selector(
    selector: dict[str, object], member: str
) -> None:
    """A member the dialect defines still has to say something usable.

    `reference_members` is a name allowlist: it decided which members may
    appear and never what they may hold, so a selected provider secret could be
    declared `optional: true` — free to resolve to nothing — or select no
    version at all, and `jobs-smoke:migration:secret_bindings` still passed.
    """

    entry = {"name": "ODP_POI_PROVIDER_API_KEY", "valueFrom": {"secretKeyRef": selector}}
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert f"valueFrom.secretKeyRef.{member}" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]
    assert "attacker-controlled-secret" not in json.dumps(report)
    assert "attacker-controlled-secret" not in detail


@pytest.mark.parametrize(("selector", "member"), _UNUSABLE_V2_SELECTORS)
def test_job_smoke_rejects_an_unusable_v2_secret_selector(
    selector: dict[str, object], member: str
) -> None:
    """The same fail-open on the Cloud Run v2 container path."""

    entry = {"name": "ODP_POI_PROVIDER_API_KEY", "valueSource": {"secretKeyRef": selector}}
    job = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job, kind="worker")
    detail = _detail(checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert f"valueSource.secretKeyRef.{member}" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]


def test_job_smoke_rejects_an_optional_database_secret_binding() -> None:
    """The database secret is mandatory under the same rule as provider keys.

    `ODAY_DATABASE_URL` is required for every selection, so a selector that
    lets Cloud Run resolve it to nothing is exactly the binding this proof
    exists to reject.
    """

    job = _knative_job(
        secret_envs=(
            {
                "name": "ODAY_DATABASE_URL",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "oday-database-url",
                        "key": "latest",
                        "optional": True,
                    }
                },
            },
            *(_knative_secret_env(env_var) for env_var in SELECTED_PROVIDER_SECRET_ENVS),
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODAY_DATABASE_URL" in detail
    assert "valueFrom.secretKeyRef.optional" in detail
    assert "ODAY_DATABASE_URL" not in report["secret_bound_env_vars"]


#: Every selector Secret Manager really resolves, including both boundaries the
#: round-10 tightening runs against: the exact lowercase `latest` literal and an
#: alias of exactly 63 characters, the longest one the service accepts.
_USABLE_VERSION_SELECTORS: tuple[object, ...] = (
    "latest",
    "1",
    "42",
    "prod_pinned",
    "prod-v1",
    pytest.param(_ALIAS_63, id="alias-63-chars"),
    pytest.param(_INT64_MAX, id="version-int64-max"),
)


@pytest.mark.parametrize("key", _USABLE_VERSION_SELECTORS)
def test_job_smoke_accepts_every_usable_knative_version_selector(key: str) -> None:
    """Fail-closed must not narrow to the one version string gcloud defaults to.

    Secret Manager resolves `latest`, a version number, and a version alias, so
    a job pinned to `1` or to an alias binds its secret just as mandatorily as
    the `latest` shape `--set-secrets` emits.
    """

    entry = {
        "name": "ODP_POI_PROVIDER_API_KEY",
        "valueFrom": {"secretKeyRef": {"name": _POI_SECRET, "key": key, "optional": False}},
    }
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job)

    assert all(check.ok for check in checks), _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in report["secret_bound_env_vars"]


@pytest.mark.parametrize("version", _USABLE_VERSION_SELECTORS)
def test_job_smoke_accepts_every_usable_v2_version_selector(version: str) -> None:
    """The same acceptance boundary on the Cloud Run v2 container path.

    The round-10 grammar is a Secret Manager fact, not a dialect fact, so both
    dialects must reject the same malformed selectors *and* keep accepting the
    same resolvable ones — otherwise a v2 job pinned to a legal 63-character
    alias would be failed by a rule written for the Knative shape.
    """

    entry = {
        "name": "ODP_POI_PROVIDER_API_KEY",
        "valueSource": {"secretKeyRef": {"secret": _POI_SECRET, "version": version}},
    }
    job = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job, kind="worker")

    assert all(check.ok for check in checks), _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in report["secret_bound_env_vars"]


def test_job_smoke_accepts_a_v2_selector_without_a_declared_version() -> None:
    """Cloud Run v2 leaves `version` optional; only v1's `key` is required.

    The required/optional split is a per-dialect API fact, so tightening the
    Knative side must not invent a requirement the v2 API does not state.
    """

    entry = {
        "name": "ODP_POI_PROVIDER_API_KEY",
        "valueSource": {"secretKeyRef": {"secret": _POI_SECRET}},
    }
    job = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job, kind="worker")

    assert "jobs-smoke:worker:secret_bindings" not in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in report["secret_bound_env_vars"]


@pytest.mark.parametrize("secret", _UNUSABLE_SECRET_NAMES)
def test_job_smoke_rejects_an_unusable_knative_secret_name(secret: object) -> None:
    """The name member has to name a secret, not merely be non-empty.

    Round 10 fixed the version member and named the rule: the description is
    the proof, so a selector that is not identical to its own `strip()` is a
    defect in what gcloud emitted. The name member was still `.strip()`ed and
    matched against no grammar, so ` odp-poi-provider-api-key `,
    `odp poi provider api key`, a 256-character name, and `.` each proved a
    mandatory binding that Secret Manager resolves to nothing.
    """

    entry = {
        "name": "ODP_POI_PROVIDER_API_KEY",
        "valueFrom": {"secretKeyRef": {"name": secret, "key": "latest"}},
    }
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert "valueFrom.secretKeyRef.name" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]


@pytest.mark.parametrize("secret", _UNUSABLE_SECRET_NAMES)
def test_job_smoke_rejects_an_unusable_v2_secret_name(secret: object) -> None:
    """The same fail-open on the Cloud Run v2 container path.

    What names a resolvable secret is a Secret Manager fact rather than a
    dialect fact, so the v2 `secret` member answers to the same grammar the
    Knative `name` member does.
    """

    entry = {
        "name": "ODP_POI_PROVIDER_API_KEY",
        "valueSource": {"secretKeyRef": {"secret": secret, "version": "latest"}},
    }
    job = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job, kind="worker")
    detail = _detail(checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in detail
    assert "valueSource.secretKeyRef.secret" in detail
    assert "ODP_POI_PROVIDER_API_KEY" not in report["secret_bound_env_vars"]


@pytest.mark.parametrize("secret", _USABLE_SECRET_NAMES)
def test_job_smoke_accepts_every_usable_knative_secret_name(secret: str) -> None:
    """Fail-closed must not narrow to the one name shape this deployment uses.

    A secret ID may hold digits, `_`, and mixed case and may run to 255
    characters, and a cross-project secret is named by its full resource path.
    Rejecting any of those would fail a supported deployment rather than a
    malformed description.
    """

    entry = {
        "name": "ODP_POI_PROVIDER_API_KEY",
        "valueFrom": {"secretKeyRef": {"name": secret, "key": "latest"}},
    }
    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            entry,
            _knative_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _knative_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job)

    assert all(check.ok for check in checks), _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in report["secret_bound_env_vars"]


@pytest.mark.parametrize("secret", _USABLE_SECRET_NAMES)
def test_job_smoke_accepts_every_usable_v2_secret_name(secret: str) -> None:
    """The same acceptance boundary on the Cloud Run v2 container path."""

    entry = {
        "name": "ODP_POI_PROVIDER_API_KEY",
        "valueSource": {"secretKeyRef": {"secret": secret, "version": "latest"}},
    }
    job = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            entry,
            _v2_secret_env("ODP_GEOCODE_PROVIDER_API_KEY"),
            _v2_secret_env("ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN"),
        )
    )

    checks, report = _job_checks(job, kind="worker")

    assert all(check.ok for check in checks), _failed_names(checks)
    assert "ODP_POI_PROVIDER_API_KEY" in report["secret_bound_env_vars"]


def _database_selector_with_out_of_range_number(
    component: str, *, name_key: str, version_key: str
) -> tuple[dict[str, object], str]:
    """Return the round-13 probe selector for `ODAY_DATABASE_URL` and its member.

    One numeric component of the mandatory database selector is replaced by the
    first decimal past the signed int64 maximum: either the version it pins or
    the project number of the cross-project path naming the secret. Everything
    else about the description stays exactly what gcloud emits, so the only
    thing under test is whether an unresolvable number still proves a mandatory
    binding.
    """

    secret = "oday-database-url"
    if component == "version":
        return {name_key: secret, version_key: _OVER_INT64}, version_key
    return {name_key: f"projects/{_OVER_INT64}/secrets/{secret}", version_key: "latest"}, name_key


@pytest.mark.parametrize("component", ("version", "project-number"))
def test_job_smoke_rejects_an_out_of_range_knative_database_number(component: str) -> None:
    """Round 13: numeric selector components were checked lexically, not bounded.

    `_SECRET_VERSION_NUMBER_PATTERN` and the numeric branch of
    `_SECRET_PROJECT_PATTERN` are both `[1-9][0-9]*`, so `9223372036854775808`
    matched each of them while Secret Manager version numbers and Cloud Resource
    Manager project numbers are int64. Planting it in the one binding no
    selection can drop left `jobs-smoke:migration:secret_bindings` reporting zero
    failed checks for a mandatory `ODAY_DATABASE_URL` that cannot resolve.
    """

    selector, member = _database_selector_with_out_of_range_number(
        component, name_key="name", version_key="key"
    )
    job = _knative_job(
        secret_envs=(
            {"name": "ODAY_DATABASE_URL", "valueFrom": {"secretKeyRef": selector}},
            *(_knative_secret_env(env_var) for env_var in SELECTED_PROVIDER_SECRET_ENVS),
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ODAY_DATABASE_URL" in detail
    assert f"valueFrom.secretKeyRef.{member}" in detail
    assert _OVER_INT64 not in detail
    assert "ODAY_DATABASE_URL" not in report["secret_bound_env_vars"]


@pytest.mark.parametrize("component", ("version", "project-number"))
def test_job_smoke_rejects_an_out_of_range_v2_database_number(component: str) -> None:
    """The same probe on the Cloud Run v2 container path.

    The int64 bound belongs to the Secret Manager and Cloud Resource Manager
    resources, not to a dialect, so both descriptions had the identical
    fail-open and both must reject the identical number.
    """

    selector, member = _database_selector_with_out_of_range_number(
        component, name_key="secret", version_key="version"
    )
    job = _v2_job_with_envs(
        secret_envs=(
            {"name": "ODAY_DATABASE_URL", "valueSource": {"secretKeyRef": selector}},
            *(_v2_secret_env(env_var) for env_var in SELECTED_PROVIDER_SECRET_ENVS),
        )
    )

    checks, report = _job_checks(job, kind="worker")
    detail = _detail(checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(checks)
    assert "ODAY_DATABASE_URL" in detail
    assert f"valueSource.secretKeyRef.{member}" in detail
    assert _OVER_INT64 not in detail
    assert "ODAY_DATABASE_URL" not in report["secret_bound_env_vars"]


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("1", True),
        ("42", True),
        pytest.param(_INT64_MAX, True, id="int64-max"),
        pytest.param(_OVER_INT64, False, id="int64-max-plus-one"),
        pytest.param(_LONG_OVER_INT64, False, id="thirty-digits"),
        pytest.param("9" * 5000, False, id="beyond-int-string-conversion-limit"),
        ("0", False),
        ("007", False),
        ("-1", False),
        ("1_0", False),
        ("", False),
        ("latest", False),
    ),
)
def test_resource_number_range_check_is_total_and_bounded(value: str, expected: bool) -> None:
    """The shared numeric guard both selector members route their digits through.

    The digit count is checked before the conversion, so a decimal past
    CPython's integer-string limit is a rejection rather than a `ValueError`
    raised out of the middle of a deployment proof.
    """

    assert validator._usable_resource_number(value) is expected


#: Ways to pad a required env-var name so that `str.strip()` maps it back onto
#: the name the proof looks for. `\xa0` is a non-breaking space, which is not
#: ASCII whitespace but is whitespace to `str.strip()` — the widest gap between
#: what the description declares and what a normalizing reader sees.
_PADDED_ENV_VAR_NAME_FORMS = (
    "  {name}  ",
    "{name} ",
    " {name}",
    "\t{name}\n",
    "{name}\xa0",
)
_REQUIRED_SECRET_ENV_VARS = ("ODAY_DATABASE_URL", "ODP_POI_PROVIDER_API_KEY")


@pytest.mark.parametrize("form", _PADDED_ENV_VAR_NAME_FORMS)
@pytest.mark.parametrize("env_var", _REQUIRED_SECRET_ENV_VARS)
def test_job_smoke_rejects_a_padded_knative_secret_env_name(env_var: str, form: str) -> None:
    """The env var naming the binding answers to the rule its members do.

    Rounds 10 and 11 stopped normalizing the two `secretKeyRef` members and
    stated the rule — the description is the proof, so the validator may not
    normalize what it checks — but the entry's own `name` was still read
    through `.strip()`. A mandatory database or selected-provider secret
    declared as `"  ODAY_DATABASE_URL  "` was therefore filed under the
    required name and proved a binding the runtime does not have, with zero
    failing checks.
    """

    padded = form.format(name=env_var)
    assert padded != env_var and padded.strip() == env_var

    job = _knative_job(
        secret_envs=tuple(
            _knative_secret_env(
                padded if candidate == env_var else candidate,
                secret=candidate.lower().replace("_", "-"),
            )
            for candidate in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        )
    )

    checks, report = _job_checks(job)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert f"{env_var}: no env binding is declared" in detail
    assert env_var not in report["secret_bound_env_vars"]


@pytest.mark.parametrize("form", _PADDED_ENV_VAR_NAME_FORMS)
@pytest.mark.parametrize("env_var", _REQUIRED_SECRET_ENV_VARS)
def test_job_smoke_rejects_a_padded_v2_secret_env_name(env_var: str, form: str) -> None:
    """The same fail-open on the Cloud Run v2 container path.

    Which env var a binding names is a property of the entry rather than of the
    dialect, so the v2 path had the identical hole.
    """

    padded = form.format(name=env_var)

    job = _v2_job_with_envs(
        secret_envs=tuple(
            _v2_secret_env(
                padded if candidate == env_var else candidate,
                secret=candidate.lower().replace("_", "-"),
            )
            for candidate in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        )
    )

    checks, report = _job_checks(job, kind="worker")
    detail = _detail(checks, "jobs-smoke:worker:secret_bindings")

    assert "jobs-smoke:worker:secret_bindings" in _failed_names(checks)
    assert f"{env_var}: no env binding is declared" in detail
    assert env_var not in report["secret_bound_env_vars"]


@pytest.mark.parametrize("form", _PADDED_ENV_VAR_NAME_FORMS)
def test_job_smoke_rejects_a_padded_knative_selection_env_name(form: str) -> None:
    """A padded selection name left the provider allowlist unprovable, not read.

    The same `.strip()` filed `" ODP_PRODUCTION_PROVIDER_IDS "` under the name
    the selection is read from, so a job that declares no readable selection at
    all was validated against one — and the secret set required of it was
    derived from a value no runtime resolves.
    """

    job = _knative_job(
        provider_ids=None,
        extra_envs=(
            {
                "name": form.format(name="ODP_PRODUCTION_PROVIDER_IDS"),
                "value": RUN_30376737123_PROVIDER_IDS,
            },
        ),
    )

    checks, report = _job_checks(job)
    failed = _failed_names(checks)

    assert "jobs-smoke:migration:provider_selection" in failed
    assert "jobs-smoke:migration:secret_bindings" in failed
    assert "the selected provider set is unprovable" in _detail(
        checks, "jobs-smoke:migration:provider_selection"
    )
    assert report["selected_provider_ids"] == []


@pytest.mark.parametrize("form", _PADDED_ENV_VAR_NAME_FORMS)
def test_job_smoke_rejects_a_padded_v2_selection_env_name(form: str) -> None:
    """The same unprovable selection on the Cloud Run v2 container path."""

    job = _v2_job_with_envs(
        provider_ids=None,
        extra_envs=(
            {
                "name": form.format(name="ODP_PRODUCTION_PROVIDER_IDS"),
                "value": RUN_30376737123_PROVIDER_IDS,
            },
        ),
    )

    checks, report = _job_checks(job, kind="worker")
    failed = _failed_names(checks)

    assert "jobs-smoke:worker:provider_selection" in failed
    assert "jobs-smoke:worker:secret_bindings" in failed
    assert report["selected_provider_ids"] == []


def test_job_smoke_rejects_a_knative_binding_beside_a_padded_twin() -> None:
    """Exact keys must not relax the shape the normalizing key already rejected.

    Under `name.strip()` an exact name beside a padded twin collapsed into one
    key and failed as an ambiguous double binding. Keying by the declared name
    would make them two separate env vars and let the exact one prove the
    binding alone, so the twin is rejected explicitly: which of the two a reader
    resolves is exactly the normalizing disagreement this round removes, and
    gcloud emits neither name.
    """

    job = _knative_job(
        secret_envs=(
            _knative_secret_env("ODAY_DATABASE_URL"),
            _knative_secret_env("  ODAY_DATABASE_URL  ", secret="some-other-secret"),
            *(_knative_secret_env(env_var) for env_var in SELECTED_PROVIDER_SECRET_ENVS),
        )
    )

    checks, report = _job_checks(job)
    failed = _failed_names(checks)

    assert "jobs-smoke:migration:secret_bindings" in failed
    assert "jobs-smoke:migration:provider_selection" in failed
    assert "differing only by surrounding whitespace" in _detail(
        checks, "jobs-smoke:migration:secret_bindings"
    )
    assert report["selected_provider_ids"] == []


def test_job_smoke_rejects_a_v2_binding_beside_a_padded_twin() -> None:
    """The same fail-closed boundary on the Cloud Run v2 container path."""

    job = _v2_job_with_envs(
        secret_envs=(
            _v2_secret_env("ODAY_DATABASE_URL"),
            _v2_secret_env("\tODAY_DATABASE_URL\n", secret="some-other-secret"),
            *(_v2_secret_env(env_var) for env_var in SELECTED_PROVIDER_SECRET_ENVS),
        )
    )

    checks, _ = _job_checks(job, kind="worker")
    failed = _failed_names(checks)

    assert "jobs-smoke:worker:secret_bindings" in failed
    assert "jobs-smoke:worker:provider_selection" in failed


def test_job_smoke_accepts_exact_env_names_beside_unrelated_ones() -> None:
    """Rejecting twins must not reject the names a real description carries.

    Every env var `product_ops/deployment/deploy_cloud_run_waji.sh` sets is an exact
    identifier, and distinct names that merely share a prefix are not twins.
    """

    job = _knative_job(
        extra_envs=(
            {"name": "ODP_REQUIRE_LIVE_DATA", "value": "true"},
            {"name": "ODP_REQUIRE_LIVE_DATA_STRICT", "value": "true"},
        )
    )

    checks, report = _job_checks(job)

    assert all(check.ok for check in checks), _failed_names(checks)
    assert "ODAY_DATABASE_URL" in report["secret_bound_env_vars"]


def test_job_smoke_accepts_each_dialect_at_its_own_container_path() -> None:
    """The discriminator must not break the two shapes gcloud really emits."""

    knative_checks, _ = _job_checks(_knative_job())
    assert all(check.ok for check in knative_checks), _failed_names(knative_checks)

    v2_checks, _ = _job_checks(_v2_job(), kind="worker")
    assert "jobs-smoke:worker:secret_bindings" not in _failed_names(v2_checks)
    assert "jobs-smoke:worker:provider_selection" not in _failed_names(v2_checks)


@pytest.mark.parametrize("provider_ids", [None, "", "  ,  "])
def test_job_smoke_fails_closed_without_a_provable_selection(provider_ids: str | None) -> None:
    job = _knative_job(provider_ids=provider_ids)

    checks, report = _job_checks(job)
    failed = _failed_names(checks)

    assert "jobs-smoke:migration:provider_selection" in failed
    assert "jobs-smoke:migration:secret_bindings" in failed
    assert report["selected_provider_ids"] == []


def test_job_smoke_fails_closed_when_selection_is_only_secret_bound() -> None:
    """A selection supplied as a secret cannot be read, so it is unprovable."""

    job = _knative_job(
        provider_ids=None,
        extra_envs=(_knative_secret_env("ODP_PRODUCTION_PROVIDER_IDS"),),
    )

    checks, _ = _job_checks(job)

    assert "jobs-smoke:migration:provider_selection" in _failed_names(checks)
    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)


def test_job_smoke_rejects_secret_refs_planted_outside_the_task_template() -> None:
    """Secrets only count where Cloud Run actually runs the task.

    Locating containers by shape meant any mapping with a `containers` key
    satisfied the proof, so a description whose real task template bound nothing
    passed by planting the same refs under `metadata`.
    """

    fully_bound = _job_container(
        kind="migration",
        sha=RUN_30376737123_SHA,
        provider_ids=RUN_30376737123_PROVIDER_IDS,
        secret_envs=tuple(
            _knative_secret_env(env_var)
            for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
        ),
    )

    planted = _knative_job(secret_envs=())
    assert isinstance(planted["metadata"], dict)
    planted["metadata"]["containers"] = [fully_bound]

    checks, report = _job_checks(planted)
    failed = _failed_names(checks)
    detail = _detail(checks, "jobs-smoke:migration:secret_bindings")

    assert "jobs-smoke:migration:provider_selection" in failed
    assert "jobs-smoke:migration:secret_bindings" in failed
    assert "metadata.containers" in detail
    assert report["selected_provider_ids"] == []
    assert "secret_bound_env_vars" not in report

    # The same planted containers with no authoritative task template at all
    # must fail closed too, rather than being adopted as the task template.
    orphaned = {
        "metadata": {"name": "oday-migration", "containers": [fully_bound]},
        "spec": {"template": {"spec": {}}},
    }

    orphan_checks, _ = _job_checks(orphaned)

    assert "jobs-smoke:migration:provider_selection" in _failed_names(orphan_checks)
    assert "jobs-smoke:migration:secret_bindings" in _failed_names(orphan_checks)


def test_job_smoke_rejects_an_ambiguous_or_unreadable_task_template() -> None:
    unbound = _job_container(
        kind="migration",
        sha=RUN_30376737123_SHA,
        provider_ids=f"{RUN_30376737123_PROVIDER_IDS},listing.partner_feed",
        secret_envs=(),
    )
    both_schemas = dict(_knative_job())
    both_schemas["template"] = {"template": {"containers": [unbound]}}

    ambiguous_checks, _ = _job_checks(both_schemas)

    assert "jobs-smoke:migration:secret_bindings" in _failed_names(ambiguous_checks)
    assert "ambiguous" in _detail(ambiguous_checks, "jobs-smoke:migration:secret_bindings")

    for empty in (
        {"spec": {"template": {"spec": {"template": {"spec": {"containers": []}}}}}},
        {"spec": {"template": {"spec": {"template": {"spec": {"containers": "poi"}}}}}},
        {"spec": {"template": {"spec": {"template": {"spec": {"containers": ["poi"]}}}}}},
        {"metadata": {"name": "oday-migration"}},
    ):
        checks, report = _job_checks(empty)

        assert "jobs-smoke:migration:provider_selection" in _failed_names(checks)
        assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
        assert report["selected_provider_ids"] == []


@pytest.mark.parametrize(
    "duplicate",
    [
        # The exploit: the narrow selection is validated while a later, wider
        # one silently selects a provider whose secret is never required.
        {
            "name": "ODP_PRODUCTION_PROVIDER_IDS",
            "value": f"{RUN_30376737123_PROVIDER_IDS},listing.partner_feed",
        },
        # An identical repeat is still ambiguous: nothing proves which one the
        # runtime reads.
        {"name": "ODP_PRODUCTION_PROVIDER_IDS", "value": RUN_30376737123_PROVIDER_IDS},
        # A plaintext occurrence beside a secret-bound one is unreadable.
        _knative_secret_env("ODP_PRODUCTION_PROVIDER_IDS"),
    ],
)
def test_job_smoke_rejects_a_duplicate_provider_selection(duplicate: dict[str, object]) -> None:
    job = _knative_job(extra_envs=(duplicate,))

    checks, report = _job_checks(job)
    failed = _failed_names(checks)

    assert "jobs-smoke:migration:provider_selection" in failed
    assert "jobs-smoke:migration:secret_bindings" in failed
    assert "ambiguous" in _detail(checks, "jobs-smoke:migration:provider_selection")
    assert report["selected_provider_ids"] == []


def _with_sidecar(job: dict[str, object], sidecar: dict[str, object]) -> dict[str, object]:
    containers = job["spec"]["template"]["spec"]["template"]["spec"]["containers"]  # type: ignore[index]
    assert isinstance(containers, list)
    containers.append(sidecar)
    return job


def test_job_smoke_rejects_a_selection_declared_by_a_second_container() -> None:
    """A second container makes the selection unprovable, not merely wider."""

    job = _with_sidecar(
        _knative_job(),
        _job_container(
            kind="migration",
            sha=RUN_30376737123_SHA,
            provider_ids=f"{RUN_30376737123_PROVIDER_IDS},listing.partner_feed",
            secret_envs=(),
        ),
    )

    checks, report = _job_checks(job)

    assert "jobs-smoke:migration:provider_selection" in _failed_names(checks)
    assert "jobs-smoke:migration:secret_bindings" in _failed_names(checks)
    assert "ambiguous" in _detail(checks, "jobs-smoke:migration:provider_selection")
    assert report["selected_provider_ids"] == []


def test_job_smoke_rejects_secrets_bound_only_by_a_sidecar_container() -> None:
    """Merging env across containers was the planting exploit one level down.

    The task container declares the selection and binds nothing; a sidecar
    inside the same authoritative task template carries the whole required
    secret set. Reading env across the container list proved secrets the task
    that runs the migration never receives, so the description is rejected.
    """

    job = _with_sidecar(
        _knative_job(secret_envs=()),
        _job_container(
            kind="migration",
            sha=RUN_30376737123_SHA,
            provider_ids=None,
            secret_envs=tuple(
                _knative_secret_env(env_var)
                for env_var in ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
            ),
        ),
    )

    checks, report = _job_checks(job)
    failed = _failed_names(checks)

    assert "jobs-smoke:migration:provider_selection" in failed
    assert "jobs-smoke:migration:secret_bindings" in failed
    assert "2 containers" in _detail(checks, "jobs-smoke:migration:secret_bindings")
    assert report["selected_provider_ids"] == []
    assert "secret_bound_env_vars" not in report


def test_job_smoke_rejects_unknown_selected_provider_id() -> None:
    job = _knative_job(provider_ids=f"{RUN_30376737123_PROVIDER_IDS},poi.not_a_provider")

    checks, _ = _job_checks(job)
    failed = _failed_names(checks)

    assert "jobs-smoke:migration:provider_selection" in failed
    assert "poi.not_a_provider" in _detail(checks, "jobs-smoke:migration:provider_selection")
    assert "jobs-smoke:migration:secret_bindings" in failed


def test_job_smoke_flags_release_allowlist_mismatch() -> None:
    job = _knative_job()

    matched, matched_report = _job_checks(job, release_provider_ids=RUN_30376737123_PROVIDER_IDS)
    assert all(check.ok for check in matched), _failed_names(matched)
    assert matched_report["release_provider_ids"] == sorted(RUN_30376737123_PROVIDER_IDS.split(","))

    mismatched, _ = _job_checks(
        job, release_provider_ids=f"{RUN_30376737123_PROVIDER_IDS},listing.partner_feed"
    )
    assert "jobs-smoke:migration:selected_provider_release_match" in _failed_names(mismatched)

    empty, _ = _job_checks(job, release_provider_ids="")
    assert "jobs-smoke:migration:selected_provider_release_match" in _failed_names(empty)


def test_job_smoke_reports_but_allows_an_unselected_bound_provider_secret() -> None:
    job = _knative_job(
        extra_envs=(_knative_secret_env("ODP_LISTING_PROVIDER_API_KEY"),),
    )

    checks, report = _job_checks(job)

    assert all(check.ok for check in checks), _failed_names(checks)
    assert report["unselected_provider_secret_env_vars"] == ["ODP_LISTING_PROVIDER_API_KEY"]


@pytest.mark.parametrize("kind", ["migration", "worker", "scheduler"])
def test_job_smoke_supports_migration_worker_and_scheduler_schemas(kind: str) -> None:
    knative_checks, _ = validator.cloud_run_job_checks(
        kind=kind,
        job_description=_knative_job(kind=kind, name=f"oday-{kind}"),
        execution=_succeeded_execution(f"oday-{kind}-00001"),
        expected_sha=RUN_30376737123_SHA,
    )
    v2_checks, v2_report = validator.cloud_run_job_checks(
        kind=kind,
        job_description=_v2_job(kind=kind),
        execution=_succeeded_execution(f"oday-{kind}-00001"),
        expected_sha=RUN_30376737123_SHA,
    )

    assert all(check.ok for check in knative_checks), _failed_names(knative_checks)
    assert all(check.ok for check in v2_checks), _failed_names(v2_checks)
    assert v2_report["secret_bound_env_vars"] == sorted(
        ("ODAY_DATABASE_URL", *SELECTED_PROVIDER_SECRET_ENVS)
    )


def test_job_smoke_rejects_failed_execution_and_missing_provider_secrets() -> None:
    job = {
        "metadata": {"name": "scheduler-job", "labels": {}},
        "spec": {
            "template": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "image": "registry/scheduler:latest",
                                    "args": [
                                        "product_ops/deployment/cloud_run_job_entrypoint.py",
                                        "scheduler",
                                    ],
                                    "env": [
                                        {
                                            "name": "ODP_PRODUCTION_PROVIDER_IDS",
                                            "value": RUN_30376737123_PROVIDER_IDS,
                                        },
                                        _knative_secret_env("ODAY_DATABASE_URL"),
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        },
    }
    execution = {
        "metadata": {"name": "scheduler-job-00001"},
        "status": {
            "succeededCount": 0,
            "failedCount": 1,
            "conditions": [{"type": "Completed", "state": "CONDITION_FAILED"}],
        },
    }

    checks, _ = validator.cloud_run_job_checks(
        kind="scheduler",
        job_description=job,
        execution=execution,
        expected_sha=EXPECTED_SHA,
    )
    failed = {check.name for check in checks if not check.ok}

    assert "jobs-smoke:scheduler:release_sha" in failed
    assert "jobs-smoke:scheduler:secret_bindings" in failed
    assert "jobs-smoke:scheduler:execution" in failed


def test_jobs_smoke_cli_uses_the_release_provider_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path = tmp_path / "migration-job.json"
    execution_path = tmp_path / "migration-execution.json"
    output_path = tmp_path / "migration-validation.json"
    job_path.write_text(json.dumps(_knative_job()), encoding="utf-8")
    execution_path.write_text(json.dumps(_succeeded_execution()), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_cloud_run_live_deployment.py",
            "jobs-smoke",
            "--job-kind=migration",
            f"--job-description={job_path}",
            f"--execution={execution_path}",
            f"--expected-sha={RUN_30376737123_SHA}",
            f"--output={output_path}",
        ],
    )

    monkeypatch.setenv("ODP_PRODUCTION_PROVIDER_IDS", RUN_30376737123_PROVIDER_IDS)
    assert validator.main() == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["release_provider_ids"] == sorted(RUN_30376737123_PROVIDER_IDS.split(","))

    monkeypatch.setenv("ODP_PRODUCTION_PROVIDER_IDS", "listing.partner_feed")
    assert validator.main() == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    failed = {check["name"] for check in report["checks"] if not check["ok"]}
    assert "jobs-smoke:migration:selected_provider_release_match" in failed


def _knative_execution(name: str, created: str, *, job: str = "worker-job") -> dict[str, object]:
    return {
        "metadata": {
            "name": name,
            "creationTimestamp": created,
            "labels": {"run.googleapis.com/job": job},
        }
    }


def _v2_execution(name: str, created: str, *, job: str = "worker-job") -> dict[str, object]:
    return {
        "name": f"projects/p/locations/asia-east1/jobs/{job}/executions/{name}",
        "createTime": created,
        "job": f"projects/p/locations/asia-east1/jobs/{job}",
    }


@pytest.mark.parametrize("builder", [_knative_execution, _v2_execution])
def test_latest_execution_resolves_newest_name_across_gcloud_schemas(builder) -> None:
    """ODP-DEPLOY-CLOUD-RUN-JOB-EXECUTION-COMPAT-001: no describe-latest dependency."""
    payload = [
        builder("worker-job-00002", "2026-07-24T10:05:00Z"),
        builder("worker-job-00001", "2026-07-24T10:00:00Z"),
        builder("worker-job-00003", "2026-07-24T10:09:31.123456789Z"),
    ]

    assert validator.resolve_latest_execution_name(payload, job="worker-job") == "worker-job-00003"
    # A single execution needs no ordering evidence at all.
    assert validator.resolve_latest_execution_name([builder("worker-job-00007", "")]) == (
        "worker-job-00007"
    )
    # gcloud emits a bare array; an API-shaped wrapper stays readable.
    assert validator.resolve_latest_execution_name({"items": payload}) == "worker-job-00003"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([], "no Cloud Run Job execution was found"),
        ({"items": []}, "no Cloud Run Job execution was found"),
        ("worker-job-00001", "must be a JSON array"),
        ([{"metadata": {"creationTimestamp": "2026-07-24T10:00:00Z"}}], "no resolvable execution"),
        (["worker-job-00001"], "is not a JSON object"),
        (
            [
                _knative_execution("worker-job-00001", "2026-07-24T10:00:00Z"),
                {"metadata": {"name": "worker-job-00002"}},
            ],
            "no creation timestamp",
        ),
        (
            [
                _knative_execution("worker-job-00001", "2026-07-24T10:00:00Z"),
                _knative_execution("worker-job-00002", "not-a-timestamp"),
            ],
            "is unparsable",
        ),
        (
            [
                _knative_execution("worker-job-00001", "2026-07-24T10:00:00Z"),
                _knative_execution("worker-job-00002", "2026-07-24T10:00:00Z"),
            ],
            "ambiguous",
        ),
    ],
)
def test_latest_execution_resolution_fails_closed(payload: object, expected: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        validator.resolve_latest_execution_name(payload)

    assert expected in str(excinfo.value)


def test_latest_execution_resolution_rejects_a_foreign_job_execution() -> None:
    payload = [_knative_execution("scheduler-job-00001", "2026-07-24T10:00:00Z", job="other-job")]

    with pytest.raises(ValueError, match="does not belong to job"):
        validator.resolve_latest_execution_name(payload, job="scheduler-job")

    # An execution name is not ownership evidence: job names may share prefixes.
    unlabelled = [
        {
            "metadata": {
                "name": "worker-job-canary-00001",
                "creationTimestamp": "2026-07-24T10:00:00Z",
            }
        }
    ]
    with pytest.raises(ValueError, match="does not belong to job"):
        validator.resolve_latest_execution_name(unlabelled, job="worker-job")


def test_latest_execution_resolution_rejects_conflicting_job_references() -> None:
    execution = _knative_execution("worker-job-00001", "2026-07-24T10:00:00Z")
    execution["job"] = "projects/p/locations/asia-east1/jobs/other-job"

    with pytest.raises(ValueError, match="does not belong to job"):
        validator.resolve_latest_execution_name([execution], job="worker-job")


def test_resolve_latest_execution_cli_prints_name_and_exits_nonzero_when_unprovable(
    tmp_path: Path,
) -> None:
    executions = tmp_path / "worker-execution-list.json"
    executions.write_text(
        json.dumps(
            [
                _knative_execution("worker-job-00001", "2026-07-24T10:00:00Z"),
                _knative_execution("worker-job-00002", "2026-07-24T10:07:00Z"),
            ]
        ),
        encoding="utf-8",
    )

    resolved = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "resolve-latest-execution",
            f"--executions={executions}",
            "--job=worker-job",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "worker-job-00002"

    executions.write_text("[]", encoding="utf-8")
    empty = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "resolve-latest-execution",
            f"--executions={executions}",
            "--job=worker-job",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert empty.returncode == 1
    assert empty.stdout.strip() == ""
    assert "fail-closed" in empty.stderr

    malformed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "resolve-latest-execution",
            f"--executions={tmp_path / 'missing.json'}",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert malformed.returncode == 1
    assert malformed.stdout.strip() == ""


def _extract_shell_function(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + len("\n}\n")]


def _run_capture_latest_execution(
    tmp_path: Path, *, executions: str, or_list_context: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Execute the deploy script's capture helper against a stubbed gcloud."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executions_file = tmp_path / "executions.json"
    executions_file.write_text(executions, encoding="utf-8")
    gcloud_log = tmp_path / "gcloud.log"
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >>"{gcloud_log}"\n'
        'case "$4" in\n'
        f'  list) cat "{executions_file}" ;;\n'
        '  describe) printf \'{"metadata": {"name": "%s"}}\\n\' "$5" ;;\n'
        '  *) echo "unexpected gcloud call: $*" >&2; exit 64 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)

    receipt = tmp_path / "worker-execution.json"
    deploy_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    capture_function = _extract_shell_function(deploy_text, "capture_latest_execution")
    capture_call = f'capture_latest_execution "worker-job" "{receipt}"'
    if or_list_context:
        capture_call += " || true"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        'GCP_REGION="asia-east1"\n'
        'GCP_PROJECT="oday-plus"\n'
        f'run_locked_python() {{ "{sys.executable}" "$@"; }}\n'
        f"{capture_function}"
        f"{capture_call}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["/bin/bash", str(harness)],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        check=False,
        capture_output=True,
        text=True,
    )
    return result, receipt, gcloud_log


def test_capture_latest_execution_describes_the_newest_execution_by_exact_name(
    tmp_path: Path,
) -> None:
    """Runtime proof: list resolves the name, describe is called with that name."""
    result, receipt, gcloud_log = _run_capture_latest_execution(
        tmp_path,
        executions=json.dumps(
            [
                _knative_execution("worker-job-00001", "2026-07-24T10:00:00Z"),
                _knative_execution("worker-job-00002", "2026-07-24T10:07:00Z"),
            ]
        ),
    )

    assert result.returncode == 0, result.stderr
    calls = gcloud_log.read_text(encoding="utf-8").splitlines()
    assert calls[0].startswith("run jobs executions list --job=worker-job")
    assert calls[1].startswith("run jobs executions describe worker-job-00002 ")
    assert "describe-latest" not in gcloud_log.read_text(encoding="utf-8")
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "metadata": {"name": "worker-job-00002"}
    }


@pytest.mark.parametrize(
    "executions",
    ["[]", '[{"metadata": {"creationTimestamp": "2026-07-24T10:00:00Z"}}]', "not-json"],
)
def test_capture_latest_execution_fails_closed_without_describing_anything(
    tmp_path: Path, executions: str
) -> None:
    result, receipt, gcloud_log = _run_capture_latest_execution(tmp_path, executions=executions)

    assert result.returncode != 0
    assert not receipt.exists()
    assert "describe" not in gcloud_log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "executions",
    ["[]", '[{"metadata": {"creationTimestamp": "2026-07-24T10:00:00Z"}}]', "not-json"],
)
def test_capture_latest_execution_fails_closed_in_execute_job_or_list_context(
    tmp_path: Path, executions: str
) -> None:
    """Failure forensics must not disable the helper's fail-closed boundary."""
    result, receipt, gcloud_log = _run_capture_latest_execution(
        tmp_path, executions=executions, or_list_context=True
    )

    # execute_job intentionally swallows forensic-capture failure, but Bash
    # disables errexit inside an OR-list function call. Explicit helper returns
    # must still prevent an empty-name describe and an unproven receipt.
    assert result.returncode == 0
    assert not receipt.exists()
    assert "describe" not in gcloud_log.read_text(encoding="utf-8")


def test_deploy_script_captures_job_proof_without_describe_latest() -> None:
    """The deploy runner's gcloud version must not decide whether proof exists."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "describe-latest" not in text
    assert "gcloud run jobs executions list" in text
    assert 'gcloud run jobs executions describe "${execution_name}"' in text
    assert "resolve-latest-execution" in text
    assert '--executions="${list_file}"' in text
    assert '--job="${job}"' in text
    # Resolution must precede the exact-name describe, which precedes the proof.
    resolve = text.index("resolve-latest-execution")
    describe = text.index('gcloud run jobs executions describe "${execution_name}"')
    assert text.index("gcloud run jobs executions list") < resolve < describe
    assert describe < text.index("validate_cloud_run_live_deployment.py jobs-smoke")
    # The resolver runs under the locked interpreter like every other validator.
    assert (
        'execution_name="$(run_locked_python \\\n'
        "    product_ops/deployment/validate_cloud_run_live_deployment.py "
        "resolve-latest-execution \\\n"
    ) in text
    # Both the success proof and the failure forensics share one resolver.
    assert text.count("capture_latest_execution ") == 2
    assert (
        'capture_latest_execution "${job}" "${JOB_REPORT_DIR}/${kind}-execution.json" || true'
    ) in text
    # A failed execution still stops the deployment and hits the rollback trap.
    failure_capture = text.index('capture_latest_execution "${job}" "${JOB_REPORT_DIR}')
    assert failure_capture < text.index(
        'echo "Error: ${kind} Cloud Run Job failed; deployment stopped." >&2'
    )
    assert "handle_deployment_exit" in text

    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_script_runs_the_live_e2e_gate_before_committing_the_release() -> None:
    """ODP-LIVE-E2E-001: a red live E2E gate must fall through to the rollback trap."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    gate = text.index("delivery_toolchain/e2e/check_live_e2e_gate.py")
    web_cut = text.index('promote_service_traffic "${WEB_SERVICE}"')
    committed = text.index("DEPLOYMENT_COMMITTED=true")

    # Promoted (so the gate exercises the release users will get) but not yet
    # committed (so `handle_deployment_exit` can still restore the old traffic).
    assert web_cut < gate < committed
    assert '--expected-sha "${ODAY_RELEASE_SHA}"' in text
    assert '--worker-job "${WORKER_CANDIDATE_JOB}"' in text
    assert '--output "${LIVE_E2E_REPORT}"' in text
    # The report lands next to the other deployment proofs so the workflow's
    # existing `.odp_data/deployment/*.json` upload picks it up.
    assert 'LIVE_E2E_REPORT="${LIVE_E2E_REPORT:-.odp_data/deployment/' in text


def test_live_e2e_gate_urls_are_resolved_before_the_gate_invocation() -> None:
    """A command substitution inside argv would hand the gate a blank URL.

    `set -e` does not trip on a failing `$( )` expanded into an argument list,
    so the gate would silently run against an empty origin. Resolving into
    variables first makes the failure fatal, and the explicit emptiness guard
    covers a helper that exits 0 with no output.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    resolve = text.index(
        'LIVE_E2E_API_URL="$(service_snapshot_url "${API_CANDIDATE_DESCRIPTION}")"'
    )
    guard = text.index('if [[ -z "${LIVE_E2E_API_URL}" || -z "${LIVE_E2E_WEB_URL}" ]]; then')
    gate = text.index("delivery_toolchain/e2e/check_live_e2e_gate.py")

    assert resolve < guard < gate
    assert '--api-url "${LIVE_E2E_API_URL}"' in text
    assert '--web-url "${LIVE_E2E_WEB_URL}"' in text
    assert '--api-url "$(service_snapshot_url' not in text
    assert '--web-url "$(service_snapshot_url' not in text


def _workflow_job_env(workflow: Path) -> dict[str, str]:
    """The literal `env:` values the deploy job exports, `${{ vars.X }}` -> "".

    Repository variables are unset by default, so an expression that only reads
    `vars.*` reaches the deploy script as an empty string. That is exactly how
    `ODP_LIVE_E2E_DEPLOYMENT_MODE` behaves on a stock repo.
    """
    env: dict[str, str] = {}
    for line in workflow.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^ {6}([A-Z][A-Z0-9_]*): (.*)$", line)
        if not match:
            continue
        name, raw = match.group(1), match.group(2).strip()
        if raw.startswith("${{"):
            # Runtime Release binds the deploy environment to the required
            # workflow input.  Use the representative allowed value here so
            # this source-level contract still evaluates the real mode wiring.
            env[name] = "dev" if "inputs.environment" in raw else ""
            continue
        env[name] = raw.strip('"')
    return env


def _deploy_script_expected_deployment(env: dict[str, str]) -> str:
    """Evaluate the deploy script's own `--expected-deployment` resolution."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assignment = next(
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("LIVE_E2E_DEPLOYMENT_MODE=")
    )
    # The gate must be handed the resolved variable, never a literal, or this
    # evaluation would not describe what the deploy actually passes.
    assert '--expected-deployment "${LIVE_E2E_DEPLOYMENT_MODE}"' in text
    completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False
        ["bash", "-c", f'{assignment}\nprintf "%s" "${{LIVE_E2E_DEPLOYMENT_MODE}}"'],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), **env},
    )
    return completed.stdout


def _api_env_payload(env: dict[str, str]) -> dict[str, str]:
    """Mirror the deploy script's API env payload for the deployment-mode keys."""
    deploy_env = env["ODP_DEPLOY_ENV"]
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'payload["ODAY_ENV"] = os.environ["ODP_DEPLOY_ENV"]' in text
    assert 'payload["ODP_ENV"] = os.environ["ODP_DEPLOY_ENV"]' in text
    return {
        "ODP_DEPLOY_ENV": deploy_env,
        "ODAY_ENV": deploy_env,
        "ODP_ENV": deploy_env,
        "ODP_PRODUCT_MODE": env.get("ODP_PRODUCT_MODE", ""),
        "ODP_REQUIRE_LIVE_DATA": env.get("ODP_REQUIRE_LIVE_DATA", ""),
    }


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_live_e2e_gate_expects_the_deployment_mode_the_runtime_will_report(
    workflow: Path,
) -> None:
    """ODP-LIVE-E2E-001: `--expected-deployment` must match `deployment_mode()`.

    `details.deploymentMode` is produced by
    `apps.api.oday_api.runtime_mode.deployment_mode()`, which reads the
    ODP_DEPLOY_ENV/ODAY_ENV/ODP_ENV triple this deploy writes into the API env
    payload. Defaulting the gate's expectation to "production" therefore made
    `runtime:readiness` unsatisfiable on every non-prod deploy — and because the
    gate runs under `set -e` before `DEPLOYMENT_COMMITTED=true`, every dev deploy
    promoted and then rolled straight back.

    Nothing is asserted against a literal here: the expectation is evaluated out
    of the deploy script by bash, and the reported mode is computed by the real
    runtime function.
    """
    from apps.api.oday_api.runtime_mode import deployment_mode, live_data_required

    env = _workflow_job_env(workflow)
    assert env.get("ODP_DEPLOY_ENV"), f"{workflow.name} declares no ODP_DEPLOY_ENV"

    expected = _deploy_script_expected_deployment(env)
    reported = deployment_mode(_api_env_payload(env))

    assert expected == reported, (
        f"{workflow.name}: gate expects deploymentMode={expected!r} but the "
        f"deployed runtime will report {reported!r}"
    )
    # Live-ness is a separate fact, carried by ODP_PRODUCT_MODE /
    # ODP_REQUIRE_LIVE_DATA. A `dev` deployment mode is still a live runtime, so
    # the gate must not infer live-ness from the env name.
    assert live_data_required(_api_env_payload(env)) is True


def test_live_e2e_gate_refuses_to_run_without_a_deployment_mode() -> None:
    """An unset ODP_DEPLOY_ENV must stop the deploy, not silently expect ""."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    guard = text.index('if [[ -z "${LIVE_E2E_DEPLOYMENT_MODE}" ]]; then')
    gate = text.index("delivery_toolchain/e2e/check_live_e2e_gate.py")

    assert guard < gate
    assert _deploy_script_expected_deployment({"ODP_DEPLOY_ENV": "staging"}) == "staging"
    assert (
        _deploy_script_expected_deployment(
            {"ODP_DEPLOY_ENV": "dev", "ODP_LIVE_E2E_DEPLOYMENT_MODE": "production"}
        )
        == "production"
    )


def test_real_app_platform_health_job_queue_contract(tmp_path: Path) -> None:
    """Regression: /platform/health job_queue text is derived from bundle.mode.

    - mode="postgresql" → positive marker passes validator gate.
    - mode="durable" (SQLite) → "sqlite" in text → fails closed (forbidden marker).
    - mode="memory" (in-memory) → "in-memory" in text → fails closed (forbidden marker).
    - bare "healthy" → missing required marker → validator rejects.

    If main.py is reverted to the old bundle.is_durable path this test fails,
    because the SQLite durable bundle would emit "durable postgresql" and
    appear to pass the validator when it should not.
    """
    import dataclasses

    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle

    # 1. mode="postgresql" bundle — constructed from a durable SQLite base with mode
    #    overridden so the engine is present for the DB query path, but the health
    #    payload reflects the honest PostgreSQL label (the only mode that should pass).
    sqlite_base = _durable_bundle(tmp_path / "test.db")
    pg_mode_bundle = dataclasses.replace(sqlite_base, mode="postgresql")
    pg_app = create_app(persistence=pg_mode_bundle)
    pg_payload = TestClient(pg_app).get("/platform/health").json()

    pg_queue_text = validator._dependency_text(pg_payload, "job_queue")
    assert "healthy" in pg_queue_text, "postgresql-mode queue must be healthy"
    assert not validator._contains_forbidden_marker(pg_queue_text), (
        f"postgresql-mode queue must not contain forbidden markers; got: {pg_queue_text!r}"
    )
    assert validator.is_valid_job_queue_health(pg_queue_text), (
        f"postgresql-mode queue must pass is_valid_job_queue_health; got: {pg_queue_text!r}"
    )

    # 2. mode="durable" (SQLite) bundle — must fail closed: "sqlite" is a forbidden marker.
    durable_bundle = _durable_bundle(tmp_path / "sqlite_test.db")
    assert durable_bundle.mode == "durable", "sanity: _durable_bundle returns mode='durable'"
    sqlite_app = create_app(persistence=durable_bundle)
    sqlite_payload = TestClient(sqlite_app).get("/platform/health").json()

    sqlite_queue_text = validator._dependency_text(sqlite_payload, "job_queue")
    assert "healthy" in sqlite_queue_text, "sqlite-mode queue payload must contain 'healthy'"
    assert "sqlite" in sqlite_queue_text, (
        f"sqlite-mode queue text must contain 'sqlite' to fail closed; got: {sqlite_queue_text!r}"
    )
    assert validator._contains_forbidden_marker(sqlite_queue_text), (
        f"sqlite-mode queue must fail closed via forbidden marker; got: {sqlite_queue_text!r}"
    )
    assert not validator.is_valid_job_queue_health(sqlite_queue_text), (
        f"sqlite-mode queue must fail is_valid_job_queue_health; got: {sqlite_queue_text!r}"
    )

    # 3. mode="memory" (in-memory) bundle — fails closed: "in-memory" is a forbidden marker.
    mem_bundle = _memory_bundle()
    mem_app = create_app(persistence=mem_bundle)
    mem_payload = TestClient(mem_app).get("/platform/health").json()

    mem_queue_text = validator._dependency_text(mem_payload, "job_queue")
    assert "healthy" in mem_queue_text, "in-memory queue payload must contain 'healthy'"
    assert validator._contains_forbidden_marker(mem_queue_text), (
        f"in-memory queue must fail closed via forbidden marker; got: {mem_queue_text!r}"
    )
    assert not validator.is_valid_job_queue_health(mem_queue_text), (
        f"in-memory queue must fail is_valid_job_queue_health; got: {mem_queue_text!r}"
    )

    # 4. Bare "healthy" payload — fails closed: no required marker.
    bare_payload = {"dependencies": {"job_queue": "healthy"}}
    bare_queue_text = validator._dependency_text(bare_payload, "job_queue")
    assert not validator.is_valid_job_queue_health(bare_queue_text), (
        f"bare 'healthy' must fail is_valid_job_queue_health; got: {bare_queue_text!r}"
    )


def test_declared_data_mode_handles_all_envelope_shapes() -> None:
    """Verify _declared_data_mode across the supported API envelopes."""
    assert validator._declared_data_mode({"modes": {"data": {"mode": "live"}}}) == "live"
    assert validator._declared_data_mode({"details": {"data": {"mode": "live"}}}) == "live"
    assert validator._declared_data_mode({"data_mode": "live"}) == "live"
    assert validator._declared_data_mode({"dataMode": "live"}) == "live"
    assert validator._declared_data_mode({"details": {"data_mode": "live"}}) == "live"
    assert validator._declared_data_mode({"meta": {"dataMode": "live"}}) == "live"
    assert validator._declared_data_mode({"dependencies": {"data_mode": "live"}}) == "live"
    assert validator._declared_data_mode({"details": {"bindingMode": "live"}}) == "live"
    assert validator._declared_data_mode({"binding_mode": "live"}) == "live"
    assert validator._declared_data_mode({}) == ""
    assert validator._declared_data_mode({"status": "ok"}) == ""


def test_declared_data_mode_prefers_canonical_root_contract() -> None:
    payload = {
        "data_mode": "fixture",
        "modes": {"data": {"mode": "live"}},
        "details": {"binding_mode": "live"},
    }

    assert validator._declared_data_mode(payload) == "fixture"


def test_real_app_health_data_mode_matches_unchanged_deploy_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real API payloads satisfy the direct live mode contract without gate changes."""
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from models.shared_ml import MlflowProductionModelRuntime
    from shared.infrastructure.persistence.factory import _memory_bundle
    from tests.integration.test_operator_live_provenance_health import (
        _live_connectivity_probe,
        _live_provider,
        _production_backed_bundle,
    )
    from tests.integration.test_production_api_composition import RecordingProductionRuntime

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.setattr(
        MlflowProductionModelRuntime,
        "from_environment",
        classmethod(lambda _cls, **_kwargs: RecordingProductionRuntime()),
    )
    live_bundle = _production_backed_bundle(tmp_path / "health-data-mode.sqlite3")
    live_app = create_app(
        persistence=live_bundle,
        external_provider_validation=_live_provider(),
        external_provider_connectivity_probe=_live_connectivity_probe,
    )

    with TestClient(live_app) as client:
        live_payloads = [
            client.get("/platform/health").json(),
            client.get("/readiness").json(),
        ]

    for payload in live_payloads:
        assert payload["status"] == "ok"
        assert payload["data_mode"] == "live"
        assert validator._declared_data_mode(payload) == "live"

    unavailable_app = create_app(persistence=_memory_bundle())
    with TestClient(unavailable_app) as client:
        unavailable_responses = [
            client.get("/platform/health"),
            client.get("/readiness"),
        ]

    for response in unavailable_responses:
        payload = response.json()
        assert response.status_code == 503
        assert payload["status"] == "unhealthy"
        assert payload["data_mode"] == "unavailable"
        assert validator._declared_data_mode(payload) == "unavailable"

    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA")
    monkeypatch.delenv("ODP_PERSISTENCE")
    fixture_app = create_app(persistence=_memory_bundle())
    with TestClient(fixture_app) as client:
        fixture_payloads = [
            client.get("/platform/health").json(),
            client.get("/readiness").json(),
        ]

    for payload in fixture_payloads:
        assert payload["status"] == "ok"
        assert payload["data_mode"] == "fixture"
        assert validator._declared_data_mode(payload) == "fixture"
        assert not (
            payload["status"] == "ok" and validator._declared_data_mode(payload) == "live"
        )


def test_deploy_dev_workflow_documents_smoke_principal_least_privilege_composite_roles() -> None:
    """ODP-OPERATOR-SMOKE-RBAC-LIVE-001: deploy-dev.yml documents composite least-privilege roles."""
    text = (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")
    assert "ODP-OPERATOR-SMOKE-RBAC-LIVE-001" in text
    assert "operations_manager" in text
    assert "model_owner" in text
    assert "data_owner" in text


# --- ODP-XR-PROVIDER-OFF-DEPLOYMENT-001 negative and invariant tests ---


def test_preflight_rejects_external_provider_live_mode() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: preflight must reject ODP_EXTERNAL_PROVIDER_MODE=live."""
    env = complete_env()
    env["ODP_EXTERNAL_PROVIDER_MODE"] = "live"
    for name in list(env.keys()):
        if any(p in name for p in ("POI", "GEOCODE", "ADMIN_BOUNDARY", "LISTING")):
            env.pop(name, None)
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {c.name: c for c in checks}
    assert "runtime:external_provider_mode_off" in by_name
    assert by_name["runtime:external_provider_mode_off"].ok is False


def test_preflight_rejects_projected_provider_secrets() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: preflight must reject any projected provider secrets."""
    env = complete_env()
    for name in list(env.keys()):
        if any(p in name for p in ("POI", "GEOCODE", "ADMIN_BOUNDARY", "LISTING")):
            env.pop(name, None)
    env["ODP_POI_PROVIDER_API_KEY_SECRET"] = "secret:1"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {c.name: c for c in checks}
    assert "runtime:no_provider_secrets_projected" in by_name
    assert by_name["runtime:no_provider_secrets_projected"].ok is False
    assert "ODP_POI_PROVIDER_API_KEY_SECRET" in by_name["runtime:no_provider_secrets_projected"].detail


def test_preflight_rejects_projected_provider_endpoints() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: preflight must reject any projected provider endpoints."""
    env = complete_env()
    for name in list(env.keys()):
        if any(p in name for p in ("POI", "GEOCODE", "ADMIN_BOUNDARY", "LISTING")):
            env.pop(name, None)
    env["ODP_GEOCODE_PROVIDER_URL"] = "https://geocode.example.test"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {c.name: c for c in checks}
    assert "runtime:no_provider_endpoints_projected" in by_name
    assert by_name["runtime:no_provider_endpoints_projected"].ok is False
    assert "ODP_GEOCODE_PROVIDER_URL" in by_name["runtime:no_provider_endpoints_projected"].detail


def test_consumer_only_job_secret_bindings_require_only_database() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: Job with no provider IDs requires only database secret."""
    sha = "a" * 40
    job_description = {
        "name": "projects/oday/locations/asia-east1/jobs/oday-worker-consumer",
        "labels": {"oday-release-sha": sha},
        "template": {
            "template": {
                "containers": [
                    {
                        "image": f"registry/worker:dev-{sha}",
                        "command": ["python"],
                        "args": ["product_ops/deployment/cloud_run_job_entrypoint.py", "worker"],
                        "env": [
                            {"name": "ODAY_RELEASE_SHA", "value": sha},
                            {
                                "name": "ODAY_DATABASE_URL",
                                "valueSource": {
                                    "secretKeyRef": {
                                        "secret": "oday-database-url",
                                        "version": "1",
                                    }
                                },
                            },
                        ],
                    }
                ]
            }
        },
    }
    execution = {
        "metadata": {"name": "oday-worker-consumer-exec-1"},
        "status": {
            "succeededCount": 1,
            "failedCount": 0,
            "completionTime": "2026-08-24T00:00:00Z",
            "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}],
        },
    }
    checks, report = validator.cloud_run_job_checks(
        kind="worker",
        job_description=job_description,
        execution=execution,
        expected_sha=sha,
    )
    by_name = {c.name: c for c in checks}
    assert by_name["jobs-smoke:worker:provider_selection"].ok is True
    assert by_name["jobs-smoke:worker:secret_bindings"].ok is True
    assert report["required_secret_env_vars"] == ["ODAY_DATABASE_URL"]


@pytest.mark.parametrize(
    "status_var",
    [
        "ODP_POI_PROVIDER_AUTH_STATUS",
        "ODP_GEOCODE_PROVIDER_AUTH_STATUS",
        "ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS",
        "ODP_LISTING_PROVIDER_AUTH_STATUS",
        "ODP_STORE_OPENING_AUTHORITY_STATUS",
    ],
)
def test_preflight_rejects_projected_provider_auth_status(status_var: str) -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: preflight must reject any projected provider auth status."""
    env = complete_env()
    for name in list(env.keys()):
        if any(p in name for p in ("POI", "GEOCODE", "ADMIN_BOUNDARY", "LISTING", "STORE_OPENING")):
            env.pop(name, None)
    env[status_var] = "active"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {c.name: c for c in checks}
    assert "runtime:no_provider_auth_status_projected" in by_name
    assert by_name["runtime:no_provider_auth_status_projected"].ok is False
    assert status_var in by_name["runtime:no_provider_auth_status_projected"].detail


def test_preflight_rejects_projected_production_provider_ids() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: preflight must reject projected ODP_PRODUCTION_PROVIDER_IDS in consumer deployment."""
    env = complete_env()
    env["ODP_PRODUCTION_PROVIDER_IDS"] = "poi.commercial_api,geocode.primary_api"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {c.name: c for c in checks}
    assert "runtime:no_production_provider_ids_projected" in by_name
    assert by_name["runtime:no_production_provider_ids_projected"].ok is False
    assert "poi.commercial_api" in by_name["runtime:no_production_provider_ids_projected"].detail


def test_preflight_rejects_projected_provider_probe_timeout() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: preflight must reject projected ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS in consumer deployment."""
    env = complete_env()
    env["ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"] = "5.0"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {c.name: c for c in checks}
    assert "runtime:no_provider_probe_timeout_projected" in by_name
    assert by_name["runtime:no_provider_probe_timeout_projected"].ok is False


def test_preflight_dynamically_rejects_all_registered_provider_env_vars() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: every provider in PROVIDER_REGISTRY has its endpoints, credentials, and statuses rejected."""
    from modules.external_data.connectors.provider_registry import PROVIDER_REGISTRY

    for provider in PROVIDER_REGISTRY:
        if provider.endpoint_env_var:
            env = complete_env()
            env[provider.endpoint_env_var] = "https://endpoint.example.test"
            checks = validator.preflight_checks(
                env=env,
                expected_environment="dev",
                expected_sha=EXPECTED_SHA,
                root=ROOT,
            )
            by_name = {c.name: c for c in checks}
            assert by_name["runtime:no_provider_endpoints_projected"].ok is False
            assert provider.endpoint_env_var in by_name["runtime:no_provider_endpoints_projected"].detail

        for cred in provider.credentials:
            if cred.env_var:
                env = complete_env()
                env[cred.env_var] = "test-secret-value"
                checks = validator.preflight_checks(
                    env=env,
                    expected_environment="dev",
                    expected_sha=EXPECTED_SHA,
                    root=ROOT,
                )
                by_name = {c.name: c for c in checks}
                assert by_name["runtime:no_provider_secrets_projected"].ok is False
                assert cred.env_var in by_name["runtime:no_provider_secrets_projected"].detail

            if cred.status_env_var and cred.status_env_var != "ODP_COMPETITOR_MANUAL_SOURCE_STATUS":
                env = complete_env()
                env[cred.status_env_var] = "active"
                checks = validator.preflight_checks(
                    env=env,
                    expected_environment="dev",
                    expected_sha=EXPECTED_SHA,
                    root=ROOT,
                )
                by_name = {c.name: c for c in checks}
                assert by_name["runtime:no_provider_auth_status_projected"].ok is False
                assert cred.status_env_var in by_name["runtime:no_provider_auth_status_projected"].detail


def test_preflight_fails_closed_when_provider_registry_cannot_be_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: preflight fails closed and early-returns when PROVIDER_REGISTRY cannot be loaded."""
    def _failing_inventory(*_args: object, **_kwargs: object):
        raise RuntimeError("simulated PROVIDER_REGISTRY load failure")

    monkeypatch.setattr(validator, "dynamic_provider_env_inventory", _failing_inventory)
    env = complete_env()
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {c.name: c for c in checks}
    assert "repository:provider_registry_inventory" in by_name
    assert by_name["repository:provider_registry_inventory"].ok is False
    assert "simulated PROVIDER_REGISTRY load failure" in by_name["repository:provider_registry_inventory"].detail
    assert not all(c.ok for c in checks)
    assert checks[-1].name == "repository:provider_registry_inventory"
    # Verify early-return fail-closed behavior: downstream provider-off checks are not evaluated
    assert "runtime:external_provider_mode_off" not in by_name
    assert "runtime:no_production_provider_ids_projected" not in by_name
    assert "runtime:ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS" not in by_name
    assert "runtime:no_provider_probe_timeout_projected" not in by_name
    assert "runtime:no_provider_secrets_projected" not in by_name
    assert "runtime:no_provider_endpoints_projected" not in by_name
    assert "runtime:no_provider_auth_status_projected" not in by_name


def test_dynamic_provider_env_inventory_returns_exact_general_keys_and_registry_vars() -> None:
    """ODP-XR-PROVIDER-OFF-DEPLOYMENT-001: dynamic inventory derives all keys directly from provider_registry."""
    from modules.external_data.connectors.provider_registry import (
        LIVE_MODE_ENV_VAR,
        PRODUCTION_PROVIDER_IDS_ENV_VAR,
        PROVIDER_PROBE_TIMEOUT_ENV_VAR,
    )

    inv = validator.dynamic_provider_env_inventory(root=ROOT)
    assert inv["live_mode_key"] == LIVE_MODE_ENV_VAR
    assert inv["production_provider_ids_key"] == PRODUCTION_PROVIDER_IDS_ENV_VAR
    assert inv["probe_timeout_key"] == PROVIDER_PROBE_TIMEOUT_ENV_VAR
    assert inv["general"] == {
        LIVE_MODE_ENV_VAR,
        PRODUCTION_PROVIDER_IDS_ENV_VAR,
        PROVIDER_PROBE_TIMEOUT_ENV_VAR,
    }
    assert "ODP_POI_PROVIDER_API_KEY" in inv["secrets"]
    assert "ODP_POI_PROVIDER_URL" in inv["endpoints"]
    assert "ODP_POI_PROVIDER_AUTH_STATUS" in inv["auth_statuses"]


# --- ODP-RUNTIME-RELEASE-API-INVOCATION-BOUNDARY-001 tests ---


def _extract_cloud_run_service_deploy_block(script_text: str, service_var: str) -> str:
    """Extract the gcloud run deploy command block for a specific service."""
    marker = f'gcloud run deploy "${{{service_var}}}"'
    start = script_text.find(marker)
    if start == -1:
        raise ValueError(f"Could not find deploy block for ${{{service_var}}}")
    lines = script_text[start:].splitlines()
    block_lines: list[str] = []
    for line in lines:
        block_lines.append(line)
        if not line.rstrip().endswith("\\"):
            break
    return "\n".join(block_lines)


def test_deploy_script_api_and_web_authentication_boundary_contract() -> None:
    """ODP-RUNTIME-RELEASE-API-INVOCATION-BOUNDARY-001:

    - API deployment must explicitly use --no-allow-unauthenticated and must not use --allow-unauthenticated.
    - Web deployment must use --allow-unauthenticated (as public entrypoint for OIDC login) and must not use --no-allow-unauthenticated.
    - Both services deploy with --no-traffic and explicit revision tags.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    api_block = _extract_cloud_run_service_deploy_block(text, "API_SERVICE")
    assert "--no-allow-unauthenticated" in api_block
    assert "--allow-unauthenticated" not in api_block.replace("--no-allow-unauthenticated", "")
    assert "--no-traffic" in api_block
    assert '--tag="${API_REVISION_TAG}"' in api_block

    web_block = _extract_cloud_run_service_deploy_block(text, "WEB_SERVICE")
    assert "--allow-unauthenticated" in web_block
    assert "--no-allow-unauthenticated" not in web_block
    assert "--no-traffic" in web_block
    assert '--tag="${WEB_REVISION_TAG}"' in web_block

    # Across the entire script, exactly one service uses --no-allow-unauthenticated (API)
    # and exactly one service uses --allow-unauthenticated (Web).
    assert text.count("--no-allow-unauthenticated") == 1
    assert text.count("--allow-unauthenticated") == 1


@pytest.mark.parametrize(
    "mutated_api_flag,mutated_web_flag,should_pass",
    [
        ("--no-allow-unauthenticated", "--allow-unauthenticated", True),
        ("--allow-unauthenticated", "--allow-unauthenticated", False),
        ("--no-allow-unauthenticated", "--no-allow-unauthenticated", False),
        ("", "--allow-unauthenticated", False),
        ("--no-allow-unauthenticated", "", False),
    ],
)
def test_deploy_script_invoker_boundary_fails_closed_when_flags_tampered(
    mutated_api_flag: str,
    mutated_web_flag: str,
    should_pass: bool,
) -> None:
    """ODP-RUNTIME-RELEASE-API-INVOCATION-BOUNDARY-001:

    Contract validation fails closed if either API or Web authentication flag is tampered with.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    mutated_text = text.replace(
        "  --no-allow-unauthenticated \\\n",
        f"  {mutated_api_flag} \\\n" if mutated_api_flag else "",
        1,
    ).replace(
        "  --allow-unauthenticated \\\n",
        f"  {mutated_web_flag} \\\n" if mutated_web_flag else "",
        1,
    )

    api_block = _extract_cloud_run_service_deploy_block(mutated_text, "API_SERVICE")
    web_block = _extract_cloud_run_service_deploy_block(mutated_text, "WEB_SERVICE")

    api_ok = (
        "--no-allow-unauthenticated" in api_block
        and "--allow-unauthenticated" not in api_block.replace("--no-allow-unauthenticated", "")
    )
    web_ok = (
        "--allow-unauthenticated" in web_block and "--no-allow-unauthenticated" not in web_block
    )
    is_valid = api_ok and web_ok
    assert is_valid is should_pass


def test_web_bff_iam_protected_api_audience_wiring_intact() -> None:
    """ODP-RUNTIME-RELEASE-API-INVOCATION-BOUNDARY-001:

    Web BFF service invokes IAM-protected API candidate using ODP_API_SERVICE_AUDIENCE and ODP_API_BASE_URL.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'API_SERVICE_AUDIENCE="$(service_snapshot_url "${API_CANDIDATE_DESCRIPTION}")"' in text
    assert 'python3 - "${WEB_ENV_FILE}" "${API_URL}" "${API_SERVICE_AUDIENCE}" <<\'PY\'' in text
    assert '"ODP_API_BASE_URL": sys.argv[2],' in text
    assert '"ODP_API_SERVICE_AUDIENCE": sys.argv[3],' in text


def test_no_duplicate_or_additional_deployment_entrypoints() -> None:
    """ODP-RUNTIME-RELEASE-API-INVOCATION-BOUNDARY-001:

    Deployments must go strictly through the single Runtime Release entrypoint.
    No second deploy script or workflow may exist.
    """
    deploy_scripts = list((ROOT / "product_ops/deployment").glob("deploy_*.sh"))
    assert len(deploy_scripts) == 1
    assert deploy_scripts[0].name == "deploy_cloud_run_waji.sh"

    workflows = list((ROOT / ".github/workflows").glob("deploy-*.yml"))
    assert len(workflows) == 1
    assert workflows[0].name == "deploy-dev.yml"

