from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

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
    env["ODP_OPERATOR_SMOKE_BEARER_TOKEN"] = "redacted-token-value"
    env.update(validator.REQUIRED_RUNTIME_VALUES)
    env["ODP_PRODUCTION_PROVIDER_IDS"] = ",".join(sorted(validator.REQUIRED_PRODUCT_PROVIDER_IDS))
    env["ODP_DEPLOY_ENV"] = "dev"
    env["ODAY_RELEASE_SHA"] = EXPECTED_SHA
    env["ODP_FORECAST_ENGINE"] = "statsforecast"
    env["ODP_FORECAST_MODEL"] = "seasonal_naive"
    return env


def _run_deploy_config_gate(
    tmp_path: Path,
    *,
    forecast_engine: str | None,
    forecast_model: str | None,
) -> subprocess.CompletedProcess[str]:
    for command in ("python3", "gcloud", "docker"):
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
        "ODP_WORKER_CRON": "*/5 * * * *",
        "ODP_SCHEDULER_CRON": "0 * * * *",
        "ODP_SCHEDULER_TIME_ZONE": "Asia/Taipei",
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
    assert "repository:operator_bootstrap_data_source" in by_name
    assert by_name["repository:provider_allowlist_runtime"].ok is True


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
    missing_provider_id = ""
    failed_provider_id = ""
    probe_age_seconds = 0

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
                    "details": {"database": {"status": "healthy", "mode": self.database_mode}},
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
    server, url = start_server()
    try:
        checks, _ = run_smoke(url)
    finally:
        server.shutdown()
        DeterministicRuntimeHandler.release_sha = EXPECTED_SHA
        DeterministicRuntimeHandler.data_mode = "live"
        DeterministicRuntimeHandler.database_mode = "postgresql"
        DeterministicRuntimeHandler.operator_source = "postgresql"

    failed = {check.name for check in checks if not check.ok}
    assert "smoke:/platform/version:release_sha" in failed
    assert "smoke:/platform/health:live_data_mode" in failed
    assert "smoke:/platform/health:database" in failed
    assert "smoke:/readiness:database" in failed
    assert "smoke:/api/v1/operator/bootstrap:provenance" in failed


def test_workflows_do_not_reference_secrets_in_step_if() -> None:
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        if_lines = [line for line in text.splitlines() if line.strip().startswith("if:")]
        assert all("secrets." not in line for line in if_lines)
        assert "env.HAS_WIF" in text
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
        assert "ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled" in text
        assert "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION_SECRET" not in text
        assert "validate_cloud_run_live_deployment.py preflight" in text
        assert "ODP_OPERATOR_SMOKE_BEARER_TOKEN" in text
        assert "ODP_AUTH_JWKS_URI" in text
        if workflow.name == "deploy-dev.yml":
            assert "ODP_AUTH_SUBJECT_ROLE_BINDINGS" in text
        assert "ODP_POI_PROVIDER_URL" in text
        assert "ODP_ADMIN_BOUNDARY_PROVIDER_URL" in text
        assert "ODP_WEB_OIDC_CLIENT_ID" in text
        assert "ODP_WEB_OIDC_CLIENT_SECRET_SECRET" in text
        assert "ODP_WEB_SESSION_SECRET_SECRET" in text


def test_dev_deploy_has_non_mutating_wif_oidc_smoke_gate() -> None:
    text = WORKFLOWS[0].read_text(encoding="utf-8")

    assert "wif-oidc-smoke:" in text
    assert "needs: [wif-oidc-smoke, e2e-operational-evidence]" in text
    assert "Authenticate to Google Cloud for read-only smoke" in text
    assert "gcloud auth list" in text
    assert 'gcloud projects describe "${GCP_PROJECT}"' in text
    assert "iamcredentials.googleapis.com" in text
    assert ":generateIdToken" in text
    assert "ODP_OPERATOR_SMOKE_SUBJECT" in text
    assert "minted token subject does not match configured smoke subject" in text
    assert "secrets.ODP_OPERATOR_SMOKE_BEARER_TOKEN" not in text

    smoke = text.split("  wif-oidc-smoke:", 1)[1].split(
        "  e2e-operational-evidence:", 1
    )[0]
    mutating_commands = (
        "gcloud run deploy",
        "gcloud run jobs",
        "gcloud scheduler jobs create",
        "gcloud projects add-iam-policy-binding",
        "gcloud storage buckets create",
        "terraform apply",
    )
    assert all(command not in smoke for command in mutating_commands)


def test_dev_deploy_installs_locked_dependencies_before_preflight() -> None:
    text = WORKFLOWS[0].read_text(encoding="utf-8")

    assert "uv sync --frozen" in text
    assert 'echo "${GITHUB_WORKSPACE}/.venv/bin" >> "${GITHUB_PATH}"' in text
    assert text.index("uv sync --frozen") < text.index(
        "validate_cloud_run_live_deployment.py preflight"
    )


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
    assert "ODAY_RELEASE_SHA" in text
    assert "ODP_REQUIRE_LIVE_DATA" in text
    assert "ODP_DATA_BINDING_MODE" in text
    assert "ODP_PERSISTENCE" in text
    assert '"ODP_POI_PROVIDER_URL",' in text
    assert '"ODP_ADMIN_BOUNDARY_PROVIDER_URL",' in text
    assert ': "${ODP_FORECAST_ENGINE:?' in text
    assert ': "${ODP_FORECAST_MODEL:?' in text
    assert '"ODP_FORECAST_ENGINE",' in text
    assert '"ODP_FORECAST_MODEL",' in text
    assert text.count('--env-vars-file="${API_ENV_FILE}"') == 4
    assert 'gcloud run jobs deploy "${MIGRATION_CANDIDATE_JOB}"' in text
    assert 'gcloud run jobs deploy "${SCHEDULER_CANDIDATE_JOB}"' in text
    assert 'gcloud run jobs deploy "${WORKER_CANDIDATE_JOB}"' in text
    assert text.index("validate_cloud_run_live_deployment.py smoke") < text.index(
        'upsert_scheduler_trigger \\\n  "${SCHEDULER_SCHEDULE_NAME}"'
    )
    assert "restore_scheduler_trigger" in text
    assert "ODP_PRODUCTION_PROVIDER_IDS" in text
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


def test_job_smoke_requires_exact_release_entrypoint_secrets_and_success() -> None:
    job = {
        "metadata": {
            "name": "worker-job",
            "labels": {"oday-release-sha": EXPECTED_SHA, "oday-runtime": "worker"},
        },
        "spec": {
            "template": {
                "template": {
                    "containers": [
                        {
                            "image": f"registry/worker:dev-{EXPECTED_SHA}",
                            "command": ["python"],
                            "args": [
                                "scripts/deployment/cloud_run_job_entrypoint.py",
                                "worker",
                            ],
                            "env": [
                                {"name": "ODAY_RELEASE_SHA", "value": EXPECTED_SHA},
                                {"name": "ODAY_DATABASE_URL", "valueSource": {}},
                                {"name": "ODP_LISTING_PROVIDER_API_KEY", "valueSource": {}},
                                {"name": "ODP_POI_PROVIDER_API_KEY", "valueSource": {}},
                                {"name": "ODP_GEOCODE_PROVIDER_API_KEY", "valueSource": {}},
                                {
                                    "name": "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN",
                                    "valueSource": {},
                                },
                            ],
                        }
                    ]
                }
            }
        },
    }
    execution = {
        "metadata": {"name": "worker-job-00001"},
        "status": {
            "succeededCount": 1,
            "failedCount": 0,
            "completionTime": "2026-07-24T10:00:00Z",
            "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}],
        },
    }

    checks, report = validator.cloud_run_job_checks(
        kind="worker",
        job_description=job,
        execution=execution,
        expected_sha=EXPECTED_SHA,
    )

    assert all(check.ok for check in checks)
    assert report["job_name"] == "worker-job"
    assert report["secret_values_redacted"] is True


def test_job_smoke_rejects_failed_execution_and_missing_provider_secrets() -> None:
    job = {
        "metadata": {"name": "scheduler-job", "labels": {}},
        "spec": {
            "template": {
                "containers": [
                    {
                        "image": "registry/scheduler:latest",
                        "args": ["scripts/deployment/cloud_run_job_entrypoint.py", "scheduler"],
                        "env": [{"name": "ODAY_DATABASE_URL", "valueSource": {}}],
                    }
                ]
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
