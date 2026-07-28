from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "scripts/deployment/validate_cloud_run_live_deployment.py"
TRAFFIC_HELPER_PATH = ROOT / "scripts/deployment/cloud_run_traffic.py"
TRAFFIC_SHELL_HELPER = ROOT / "scripts/deployment/cloud_run_release_traffic.sh"
SCHEDULER_HELPER_PATH = ROOT / "scripts/deployment/cloud_scheduler_trigger.py"
DEPLOY_SCRIPT = ROOT / "scripts/deploy_cloud_run_waji.sh"
WORKFLOWS = (
    ROOT / ".github/workflows/deploy-dev.yml",
    ROOT / ".github/workflows/deploy-staging.yml",
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
    env["ODP_PRODUCTION_PROVIDER_IDS"] = ",".join(sorted(validator.REQUIRED_PRODUCT_PROVIDER_IDS))
    env["ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"] = "8"
    env["ODP_DEPLOY_ENV"] = "dev"
    env["ODAY_RELEASE_SHA"] = EXPECTED_SHA
    env["ODP_FORECAST_ENGINE"] = "statsforecast"
    env["ODP_FORECAST_MODEL"] = "seasonal_naive"
    for provider in validator._provider_definitions(ROOT):
        if provider.provider_id not in validator.REQUIRED_PRODUCT_PROVIDER_IDS:
            continue
        if provider.endpoint_env_var:
            env[provider.endpoint_env_var] = f"https://{provider.provider_id}.example.test/snapshot"
        for credential in provider.credentials:
            if credential.required_in_live:
                env[f"{credential.env_var}_SECRET"] = "provider-secret:latest"
                if credential.status_env_var:
                    env[credential.status_env_var] = "active"
    return env


def test_preflight_does_not_require_unselected_listing_partner_config() -> None:
    env = complete_env()
    for name in (
        "ODP_LISTING_PROVIDER_FEED_URL",
        "ODP_LISTING_PROVIDER_AUTH_STATUS",
        "ODP_LISTING_PROVIDER_API_KEY_SECRET",
    ):
        env.pop(name, None)

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
    for name in (
        "ODP_POI_PROVIDER_URL",
        "ODP_GEOCODE_PROVIDER_URL",
        "ODP_ADMIN_BOUNDARY_PROVIDER_URL",
    ):
        assert by_name[f"config:{name}"].ok is True


def test_preflight_requires_listing_config_when_listing_is_selected() -> None:
    env = complete_env()
    env["ODP_PRODUCTION_PROVIDER_IDS"] += ",listing.partner_feed"
    env.pop("ODP_LISTING_PROVIDER_FEED_URL", None)
    env.pop("ODP_LISTING_PROVIDER_AUTH_STATUS", None)
    env.pop("ODP_LISTING_PROVIDER_API_KEY_SECRET", None)

    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["config:ODP_LISTING_PROVIDER_FEED_URL"].ok is False
    assert by_name["config:ODP_LISTING_PROVIDER_AUTH_STATUS"].ok is False
    assert by_name["secret-reference:ODP_LISTING_PROVIDER_API_KEY_SECRET"].ok is False


@pytest.mark.parametrize("value", ["", "0", "10.01", "nan", "infinity", "not-a-number"])
def test_preflight_rejects_missing_or_unbounded_provider_probe_timeout(value: str) -> None:
    env = complete_env()
    env["ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"] = value

    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    timeout_check = by_name["runtime:ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"]
    assert timeout_check.ok is False
    assert "between 0.05 and 10 seconds" in timeout_check.detail


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
        "run_locked_python scripts/deployment/validate_cloud_run_live_deployment.py preflight",
        "run_locked_python scripts/deployment/validate_cloud_run_live_deployment.py jobs-smoke",
        "run_locked_python "
        "scripts/deployment/validate_cloud_run_live_deployment.py compatibility-smoke",
        "run_locked_python scripts/deployment/validate_cloud_run_live_deployment.py smoke",
        "run_locked_python scripts/e2e/check_live_e2e_gate.py",
    ):
        assert invocation in text

    assert "python3 scripts/deployment/validate_cloud_run_live_deployment.py" not in text
    assert "python3 scripts/e2e/check_live_e2e_gate.py" not in text
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
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
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
    for provider_id in validator.REQUIRED_PRODUCT_PROVIDER_IDS:
        assert checks[f"repository:provider_adapter:{provider_id}"]["ok"] is True
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
    env["ODP_PRODUCTION_PROVIDER_IDS"] += ",competitor.manual_source"
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


def test_preflight_rejects_missing_config_memory_and_fixture_modes() -> None:
    env = complete_env()
    env["GCP_PROJECT"] = ""
    env["ODP_PERSISTENCE"] = "memory"
    env["ODP_EXTERNAL_PROVIDER_MODE"] = "fixture"
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=EXPECTED_SHA,
        root=ROOT,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["config:GCP_PROJECT"].ok is False
    assert by_name["runtime:ODP_PERSISTENCE"].ok is False
    assert by_name["runtime:ODP_EXTERNAL_PROVIDER_MODE"].ok is False


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
        assert "ODP_PRODUCTION_PROVIDER_IDS" in text
        assert (
            "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS: "
            "${{ vars.ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS }}" in text
        )
        assert "ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled" in text
        assert "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION_SECRET" not in text
        assert "validate_cloud_run_live_deployment.py preflight" in text
        assert "ODP_OPERATOR_SMOKE_BEARER_TOKEN" not in text
        assert "ODP_AUTH_JWKS_URI" in text
        assert "ODP_POI_PROVIDER_URL" in text
        assert "ODP_ADMIN_BOUNDARY_PROVIDER_URL" in text
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
        "scripts/deployment/validate_cloud_run_live_deployment.py preflight" in text
    )
    assert "python3 scripts/deployment/validate_cloud_run_live_deployment.py" not in text


def test_provider_probe_timeout_band_matches_runtime_connector() -> None:
    """The governed dev value for ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS
    derives from the runtime connector's own default, and the preflight's
    accepted band must stay aligned with the connector's clamp band. Drift on
    either side re-opens the failure this task closed: a value the connector
    accepts that the preflight rejects (or vice versa).
    """
    from modules.external_data.connectors import provider_connectivity as connectivity

    # The connector clamps with _bounded_float(minimum=0.05, maximum=MAX_...).
    assert validator.MIN_PROVIDER_PROBE_TIMEOUT_SECONDS == 0.05
    assert validator.MAX_PROVIDER_PROBE_TIMEOUT_SECONDS == connectivity.MAX_PROBE_TIMEOUT_SECONDS
    check = validator._bounded_provider_probe_timeout_check(
        {
            "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS": str(
                connectivity.DEFAULT_PROBE_TIMEOUT_SECONDS
            )
        }
    )
    assert check.ok, check.detail


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
    assert "ODP_DATA_BINDING_MODE" in text
    assert "ODP_PERSISTENCE" in text
    assert '"ODP_POI_PROVIDER_URL",' in text
    assert '"ODP_ADMIN_BOUNDARY_PROVIDER_URL",' in text
    assert 'case "${provider_id}" in' in text
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
    assert "ODP_PRODUCTION_PROVIDER_IDS" in text
    assert ': "${ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS:?' in text
    assert '"ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS",' in text
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
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >>"${GCLOUD_LOG}"\n',
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
            'ENTRYPOINT ["python", "scripts/deployment/cloud_run_job_entrypoint.py"]' in dockerfile
        )
        assert '"alembic>=1.13"' in dockerfile
        assert '"psycopg[binary,pool]>=3.2"' in dockerfile
    assert 'CMD ["worker", "--max-jobs", "100"]' in worker
    assert 'CMD ["scheduler"]' in scheduler


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
        "args": ["scripts/deployment/cloud_run_job_entrypoint.py", mode],
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
                                        "scripts/deployment/cloud_run_job_entrypoint.py",
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
        "    scripts/deployment/validate_cloud_run_live_deployment.py "
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

    gate = text.index("scripts/e2e/check_live_e2e_gate.py")
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
    gate = text.index("scripts/e2e/check_live_e2e_gate.py")

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
            env[name] = ""
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
    gate = text.index("scripts/e2e/check_live_e2e_gate.py")

    assert guard < gate
    assert _deploy_script_expected_deployment({"ODP_DEPLOY_ENV": "staging"}) == "staging"
    assert (
        _deploy_script_expected_deployment(
            {"ODP_DEPLOY_ENV": "dev", "ODP_LIVE_E2E_DEPLOYMENT_MODE": "production"}
        )
        == "production"
    )
