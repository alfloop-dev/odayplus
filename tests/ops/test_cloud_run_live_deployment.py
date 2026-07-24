from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "scripts/deployment/validate_cloud_run_live_deployment.py"
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


def complete_env() -> dict[str, str]:
    env = {
        name: f"configured-{name.lower()}"
        for name in validator.REQUIRED_PUBLIC_CONFIG
    }
    env.update(
        {
            name: f"secret-name-{index}:latest"
            for index, name in enumerate(validator.REQUIRED_SECRET_REFERENCES)
        }
    )
    env["ODP_OPERATOR_SMOKE_BEARER_TOKEN"] = "redacted-token-value"
    env.update(validator.REQUIRED_RUNTIME_VALUES)
    env["ODP_PRODUCTION_PROVIDER_IDS"] = ",".join(
        sorted(validator.REQUIRED_PRODUCT_PROVIDER_IDS)
    )
    env["ODP_DEPLOY_ENV"] = "dev"
    env["ODAY_RELEASE_SHA"] = EXPECTED_SHA
    return env


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
    assert "repository:operator_bootstrap_data_source" in by_name
    assert by_name["repository:provider_allowlist_runtime"].ok is True


def test_preflight_imports_every_registry_provider_adapter() -> None:
    checks = validator.provider_adapter_checks(ROOT)
    by_name = {check.name: check for check in checks}

    assert by_name["repository:provider_adapter:listing.partner_feed"].ok is True
    assert by_name["repository:provider_adapter:geocode.primary_api"].ok is True
    assert by_name["repository:provider_adapter:poi.commercial_api"].ok is False
    assert "PoiCommercialApiProvider" in by_name[
        "repository:provider_adapter:poi.commercial_api"
    ].detail
    assert by_name["repository:provider_adapter:admin_boundary.official_dataset"].ok is False
    assert "AdminBoundaryDatasetProvider" in by_name[
        "repository:provider_adapter:admin_boundary.official_dataset"
    ].detail
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
    assert "competitor.manual_source" in by_name[
        "runtime:production_provider_licenses"
    ].detail
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


class LiveRuntimeHandler(BaseHTTPRequestHandler):
    release_sha = EXPECTED_SHA
    data_mode = "live"
    database_mode = "postgresql"
    operator_source = "postgresql"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/platform/version":
            self._json({"status": "ok", "release_sha": self.release_sha})
            return
        if self.path == "/platform/health":
            self._json(
                {
                    "status": "ok",
                    "data_mode": self.data_mode,
                    "dependencies": {
                        "database": {"status": "healthy", "mode": self.database_mode},
                        "job_queue": {"status": "healthy", "mode": "cloud-run-worker"},
                        "external_providers": {"status": "healthy", "mode": "live"},
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
                        "database": {"status": "healthy", "mode": self.database_mode}
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
                    },
                    "today": {"queue": []},
                }
            )
            return
        if self.path == "/operator":
            body = b"<!doctype html><html><body>ODay Plus Operator</body></html>"
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), LiveRuntimeHandler)
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


def test_smoke_requires_exact_sha_live_readiness_and_non_seed_operator() -> None:
    server, url = start_server()
    try:
        checks, report = run_smoke(url)
    finally:
        server.shutdown()

    assert all(check.ok for check in checks)
    assert report["version"]["release_sha"] == EXPECTED_SHA
    assert report["operator_bootstrap"]["data_mode"] == "live"
    assert report["secret_values_redacted"] is True


def test_smoke_rejects_wrong_sha_memory_runtime_and_seed_operator() -> None:
    LiveRuntimeHandler.release_sha = "b" * 40
    LiveRuntimeHandler.data_mode = "fixture"
    LiveRuntimeHandler.database_mode = "in-memory"
    LiveRuntimeHandler.operator_source = "canonical-r4-seed"
    server, url = start_server()
    try:
        checks, _ = run_smoke(url)
    finally:
        server.shutdown()
        LiveRuntimeHandler.release_sha = EXPECTED_SHA
        LiveRuntimeHandler.data_mode = "live"
        LiveRuntimeHandler.database_mode = "postgresql"
        LiveRuntimeHandler.operator_source = "postgresql"

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
        assert "env.HAS_GCP_SA_KEY" in text
        assert "ODP_REQUIRE_LIVE_DATA: \"true\"" in text
        assert "ODP_DATA_BINDING_MODE: live" in text
        assert "ODP_PERSISTENCE: postgresql" in text
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


def test_deploy_script_preflights_before_build_and_uses_secret_references() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert text.index("validate_cloud_run_live_deployment.py preflight") < text.index(
        "docker build"
    )
    assert text.index('execute_job "migration" "${MIGRATION_JOB}"') < text.index(
        'gcloud run deploy "${API_SERVICE}"'
    )
    assert 'gcloud run jobs deploy "${MIGRATION_JOB}"' in text
    assert 'gcloud run jobs deploy "${WORKER_JOB}"' in text
    assert 'gcloud run jobs deploy "${SCHEDULER_JOB}"' in text
    assert 'execute_job "worker" "${WORKER_JOB}"' in text
    assert 'execute_job "scheduler" "${SCHEDULER_JOB}"' in text
    assert "gcloud scheduler jobs" in text
    assert "jobs-smoke" in text
    assert "validate_cloud_run_live_deployment.py smoke" in text
    assert "--set-secrets=\"${API_SECRET_BINDINGS}\"" in text
    assert "ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}" in text
    assert "ODAY_RELEASE_SHA" in text
    assert "ODP_REQUIRE_LIVE_DATA" in text
    assert "ODP_DATA_BINDING_MODE" in text
    assert "ODP_PERSISTENCE" in text
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
        assert 'ENTRYPOINT ["python", "scripts/deployment/cloud_run_job_entrypoint.py"]' in dockerfile
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
