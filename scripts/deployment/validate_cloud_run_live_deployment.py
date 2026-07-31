#!/usr/bin/env python3
"""Fail-closed preflight and smoke validation for Cloud Run deployments.

The deployment contract deliberately rejects a configured-looking environment
when the repository can only start memory/fixture-backed services. Secret
values are consumed for authenticated smoke requests but are never emitted.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import importlib
import inspect
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change-me",
    "dummy",
    "example",
    "fixture",
    "mock",
    "placeholder",
    "seed",
    "todo",
}
FORBIDDEN_DATA_MARKERS = ("fixture", "mock", "seed", "in-memory", "sqlite")
PRODUCTION_PROVIDER_IDS_ENV = "ODP_PRODUCTION_PROVIDER_IDS"
PROVIDER_PROBE_TIMEOUT_ENV = "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"
MIN_PROVIDER_PROBE_TIMEOUT_SECONDS = 0.05
MAX_PROVIDER_PROBE_TIMEOUT_SECONDS = 10.0
# See modules.external_data.connectors.provider_registry.REQUIRED_PRODUCTION_PROVIDER_IDS
# for the rationale: listing.partner_feed is a fully-implemented bulk channel that
# requires a signed licensed-data partner (absent today). Listings are sourced live
# through the assisted-listing-intake subsystem instead, so the partner feed is not
# part of the standing live-required set.
REQUIRED_PRODUCT_PROVIDER_IDS = frozenset(
    {
        "poi.commercial_api",
        "geocode.primary_api",
        "admin_boundary.official_dataset",
    }
)
MAX_PROVIDER_PROBE_AGE_SECONDS = 300
POSTGRES_PERSISTENCE_MODES = frozenset({"postgres", "postgresql"})
OPERATOR_BOOTSTRAP_CHECK = "repository:operator_bootstrap_data_source"
OPERATOR_WIRING_CHECK = "repository:operator_production_wiring"
OPERATOR_FIXTURE_WIRING_CHECK = "repository:operator_fixture_wiring_blocked"
OPERATOR_TENANT_CHECK = "repository:operator_tenant_scope_fail_closed"
OPERATOR_PROBE_CHECK = "repository:operator_live_probe_contract"

REQUIRED_PUBLIC_CONFIG = (
    "GCP_PROJECT",
    "GCP_REGION",
    "GCP_AR_REPO",
    "GCP_CLOUD_SQL_INSTANCE",
    "API_SERVICE",
    "WEB_SERVICE",
    "MIGRATION_JOB",
    "WORKER_JOB",
    "SCHEDULER_JOB",
    "WORKER_SCHEDULE_NAME",
    "SCHEDULER_SCHEDULE_NAME",
    "ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT",
    "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT",
    "ODP_WORKER_CRON",
    "ODP_SCHEDULER_CRON",
    "ODP_SCHEDULER_TIME_ZONE",
    "ODP_SNAPSHOT_BUCKET",
    "MLFLOW_TRACKING_URI",
    "ODP_FORECAST_ENGINE",
    "ODP_FORECAST_MODEL",
    PRODUCTION_PROVIDER_IDS_ENV,
    PROVIDER_PROBE_TIMEOUT_ENV,
    "ODP_AUTH_ISSUER",
    "ODP_AUTH_AUDIENCES",
    "ODP_AUTH_JWKS_URI",
    "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT",
    "ODP_WEB_OIDC_ISSUER",
    "ODP_WEB_OIDC_CLIENT_ID",
    "ODP_OPERATOR_SMOKE_ROLE",
)
REQUIRED_SECRET_REFERENCES = (
    "ODAY_DATABASE_URL_SECRET",
    "ODP_AUTH_PRINCIPAL_MAP_SECRET",
    "ODP_WEB_OIDC_CLIENT_SECRET_SECRET",
    "ODP_WEB_SESSION_SECRET_SECRET",
)
REQUIRED_SECRET_VALUES: tuple[str, ...] = ()
# The database binding is required by every Cloud Run Job regardless of which
# external providers the release selects.
DATABASE_SECRET_ENV = "ODAY_DATABASE_URL"
REQUIRED_RUNTIME_VALUES = {
    "ODP_REQUIRE_LIVE_DATA": "true",
    "ODP_DATA_BINDING_MODE": "live",
    "ODP_PRODUCT_MODE": "production",
    "ODP_EXTERNAL_PROVIDER_MODE": "live",
    "ODP_PERSISTENCE": "postgresql",
    "ODP_OBJECT_STORE": "gcs",
    "ODP_COMPETITOR_MANUAL_SOURCE_STATUS": "disabled",
}
SUPPORTED_FORECAST_BINDINGS = frozenset(
    {
        ("statsforecast", "seasonal_naive"),
        ("statsforecast", "auto_arima"),
        ("statsforecast", "auto_ets"),
        ("mlforecast", "hist_gradient_boosting"),
    }
)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    name: str
    detail: str


def _configured(value: str) -> bool:
    return value.strip().lower() not in PLACEHOLDER_VALUES


def _bounded_provider_probe_timeout_check(env: Mapping[str, str]) -> CheckResult:
    raw_value = env.get(PROVIDER_PROBE_TIMEOUT_ENV, "").strip()
    try:
        timeout_seconds = float(raw_value)
    except ValueError:
        timeout_seconds = math.nan
    ok = (
        math.isfinite(timeout_seconds)
        and MIN_PROVIDER_PROBE_TIMEOUT_SECONDS
        <= timeout_seconds
        <= MAX_PROVIDER_PROBE_TIMEOUT_SECONDS
    )
    return CheckResult(
        ok=ok,
        name=f"runtime:{PROVIDER_PROBE_TIMEOUT_ENV}",
        detail=(
            f"bounded={timeout_seconds:g}s"
            if ok
            else (
                "must be a finite number between "
                f"{MIN_PROVIDER_PROBE_TIMEOUT_SECONDS:g} and "
                f"{MAX_PROVIDER_PROBE_TIMEOUT_SECONDS:g} seconds"
            )
        ),
    )


def _write_report(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def repository_capability_checks(
    root: Path = ROOT,
    *,
    production_provider_ids: frozenset[str] = REQUIRED_PRODUCT_PROVIDER_IDS,
) -> list[CheckResult]:
    """Return source-backed production runtime capability checks.

    These checks intentionally have no environment-variable override. A deploy
    operator cannot turn an absent adapter or worker into a supported runtime by
    setting a flag.
    """

    factory = root / "shared/infrastructure/persistence/factory.py"
    factory_text = factory.read_text(encoding="utf-8") if factory.exists() else ""
    postgres_adapter_files = (
        root / "shared/infrastructure/persistence/postgres.py",
        root / "shared/infrastructure/persistence/postgresql.py",
    )
    has_postgres_adapter = any(path.exists() for path in postgres_adapter_files)
    factory_selects_postgres = bool(
        re.search(
            r"(resolved_mode|_PRODUCTION_MODES)[^\n]*(postgres|postgresql)",
            factory_text,
            flags=re.IGNORECASE,
        )
    )

    deploy_script = root / "scripts/deploy_cloud_run_waji.sh"
    deploy_text = deploy_script.read_text(encoding="utf-8") if deploy_script.exists() else ""
    worker_dockerfile = root / "infra/docker/worker.Dockerfile"
    scheduler_dockerfile = root / "infra/docker/scheduler.Dockerfile"
    job_entrypoint = root / "scripts/deployment/cloud_run_job_entrypoint.py"
    worker_text = (
        worker_dockerfile.read_text(encoding="utf-8") if worker_dockerfile.exists() else ""
    )
    scheduler_text = (
        scheduler_dockerfile.read_text(encoding="utf-8") if scheduler_dockerfile.exists() else ""
    )
    entrypoint_text = job_entrypoint.read_text(encoding="utf-8") if job_entrypoint.exists() else ""
    deploys_worker = all(
        marker in deploy_text
        for marker in (
            'gcloud run jobs deploy "${WORKER_CANDIDATE_JOB}"',
            'execute_job "worker" "${WORKER_CANDIDATE_JOB}"',
            '"${WORKER_SCHEDULE_NAME}" \\\n  "${WORKER_CANDIDATE_JOB}"',
        )
    )
    deploys_scheduler = all(
        marker in deploy_text
        for marker in (
            'gcloud run jobs deploy "${SCHEDULER_CANDIDATE_JOB}"',
            'execute_job "scheduler" "${SCHEDULER_CANDIDATE_JOB}"',
            '"${SCHEDULER_SCHEDULE_NAME}" \\\n  "${SCHEDULER_CANDIDATE_JOB}"',
        )
    )
    deploys_migration_first = (
        all(
            marker in deploy_text
            for marker in (
                'gcloud run jobs deploy "${MIGRATION_CANDIDATE_JOB}"',
                'execute_job "migration" "${MIGRATION_CANDIDATE_JOB}"',
                "compatibility-smoke",
                "run_migration_compatibility_gate",
                "jobs-smoke",
            )
        )
        and all(
            marker in entrypoint_text
            for marker in (
                "build_migration_run",
                "_verify_runtime_schema",
                "runtime_schema_verified=True",
            )
        )
        and deploy_text.index('execute_job "migration" "${MIGRATION_CANDIDATE_JOB}"')
        < deploy_text.index('gcloud run deploy "${API_SERVICE}"')
    )
    release_traffic_wired = all(
        marker in deploy_text
        for marker in (
            "--no-traffic",
            '--revision-suffix="${REVISION_SUFFIX}"',
            '--tag="${API_REVISION_TAG}"',
            '--tag="${WEB_REVISION_TAG}"',
            'capture_service_traffic "${API_SERVICE}"',
            'capture_service_traffic "${WEB_SERVICE}"',
            "rollback_release_traffic",
            'promote_service_traffic "${API_SERVICE}"',
            'promote_service_traffic "${WEB_SERVICE}"',
        )
    ) and deploy_text.index("validate_cloud_run_live_deployment.py smoke") < deploy_text.index(
        'promote_service_traffic "${API_SERVICE}"'
    ) < deploy_text.index('promote_service_traffic "${WEB_SERVICE}"')
    worker_receipt_contract = all(
        marker in entrypoint_text
        for marker in (
            "TrackingJobQueue",
            "EXIT_RETRY_QUEUED",
            "JobStatus.SUCCEEDED",
            "JobStatus.QUEUED",
            "JobStatus.FAILED",
            "JobStatus.CANCELLED",
            "claimed_job_receipt_missing",
        )
    )
    scheduler_receipt_contract = all(
        marker in entrypoint_text
        for marker in (
            "tracking_queue.enqueued",
            "no_enqueue_receipt",
            "enqueue_receipt_not_persisted",
        )
    )
    worker_runtime_wired = (
        worker_dockerfile.exists()
        and "cloud_run_job_entrypoint.py" in worker_text
        and deploys_worker
        and worker_receipt_contract
    )
    scheduler_runtime_wired = (
        scheduler_dockerfile.exists()
        and "cloud_run_job_entrypoint.py" in scheduler_text
        and deploys_scheduler
        and scheduler_receipt_contract
    )

    provider_registry = root / "modules/external_data/connectors/provider_registry.py"
    provider_registry_text = (
        provider_registry.read_text(encoding="utf-8") if provider_registry.exists() else ""
    )
    registry_honors_production_allowlist = (
        "PRODUCTION_PROVIDER_IDS_ENV_VAR" in provider_registry_text
        and "selected_provider_ids" in provider_registry_text
    )

    checks = [
        CheckResult(
            ok=has_postgres_adapter and factory_selects_postgres,
            name="repository:production_database_adapter",
            detail=(
                "supported PostgreSQL persistence adapter is wired through build_persistence"
                if has_postgres_adapter and factory_selects_postgres
                else (
                    "missing: build_persistence supports only memory/SQLite; "
                    "unsupported PostgreSQL modes fail closed"
                )
            ),
        ),
        CheckResult(
            ok=worker_runtime_wired,
            name="repository:worker_runtime",
            detail=(
                "worker image, Cloud Run Job execution, schedule, and terminal receipt checks are wired"
                if worker_runtime_wired
                else "missing: worker image/job/trigger or terminal queue receipt validation"
            ),
        ),
        CheckResult(
            ok=scheduler_runtime_wired,
            name="repository:scheduler_runtime",
            detail=(
                "scheduler image, Cloud Run Job execution, schedule, and enqueue receipt checks are wired"
                if scheduler_runtime_wired
                else "missing: scheduler image/job/trigger or enqueue receipt validation"
            ),
        ),
        CheckResult(
            ok=deploys_migration_first,
            name="repository:migration_runtime",
            detail=(
                "migration Cloud Run Job executes and validates before API/worker/scheduler deployment"
                if deploys_migration_first
                else "missing: migration Job execution and proof must precede all runtime deployment"
            ),
        ),
        CheckResult(
            ok=release_traffic_wired,
            name="repository:release_traffic",
            detail=(
                "candidate revisions deploy without traffic, pass smoke, then promote with rollback armed"
                if release_traffic_wired
                else "missing: no-traffic candidate smoke, ordered promotion, or traffic rollback"
            ),
        ),
        CheckResult(
            ok=registry_honors_production_allowlist,
            name="repository:provider_allowlist_runtime",
            detail=(
                f"provider registry honors {PRODUCTION_PROVIDER_IDS_ENV}"
                if registry_honors_production_allowlist
                else (
                    f"missing: provider startup validation does not honor "
                    f"{PRODUCTION_PROVIDER_IDS_ENV}; it validates disabled providers too"
                )
            ),
        ),
    ]
    checks.extend(operator_runtime_checks(root))
    checks.extend(provider_adapter_checks(root, production_provider_ids=production_provider_ids))
    checks.extend(observability_runtime_checks(root))
    return checks


def observability_runtime_checks(root: Path = ROOT) -> list[CheckResult]:
    """Verify live-wired observability components (exporter, dashboards, watch window, readback receipts, fail-closed gates)."""
    checks = []
    try:
        from modules.notifications import get_notification_adapter
        from shared.observability import (
            ProductionMetricsExporter,
            default_registry,
            record_deployment_watch_window_status,
            render_dashboard_provisioning,
        )

        test_sha = "10c620969a90627e4a67053a4708658f99faa07f"
        registry = default_registry()
        monitoring_route = "https://monitoring.googleapis.com/v3"

        mock_time_series_store: list[dict] = []

        def mock_provider_transport(
            method: str,
            url: str,
            params: dict | None = None,
            payload: dict | None = None,
        ) -> tuple[int, dict]:
            p_dict = payload or {}
            pr_dict = params or {}
            g_proj = (
                p_dict.get("gcp_project") or pr_dict.get("gcp_project") or "alfaloop-data-project"
            )
            r_sha = p_dict.get("release_sha") or pr_dict.get("release_sha") or test_sha

            if "timeSeries" in url:
                if method == "POST":
                    if isinstance(p_dict, dict) and "timeSeries" in p_dict:
                        mock_time_series_store.clear()
                        mock_time_series_store.extend(p_dict["timeSeries"])
                    return 200, {}
                elif method == "GET":
                    now_dt = datetime.now(UTC)
                    now_iso = now_dt.isoformat()
                    past_iso = (now_dt - timedelta(minutes=15)).isoformat()
                    if mock_time_series_store:
                        ts_return = mock_time_series_store
                    else:
                        ts_return = [
                            {
                                "metric": {
                                    "type": "custom.googleapis.com/api_error_count",
                                    "labels": {"release_sha": r_sha},
                                },
                                "resource": {"type": "global", "labels": {"project_id": g_proj}},
                                "points": [
                                    {
                                        "interval": {"endTime": past_iso},
                                        "value": {"doubleValue": 0.0},
                                    },
                                    {
                                        "interval": {"endTime": now_iso},
                                        "value": {"doubleValue": 0.0},
                                    },
                                ],
                            },
                            {
                                "metric": {
                                    "type": "custom.googleapis.com/api_latency_ms",
                                    "labels": {"release_sha": r_sha},
                                },
                                "resource": {"type": "global", "labels": {"project_id": g_proj}},
                                "points": [
                                    {
                                        "interval": {"endTime": past_iso},
                                        "value": {"doubleValue": 12.5},
                                    },
                                    {
                                        "interval": {"endTime": now_iso},
                                        "value": {"doubleValue": 14.2},
                                    },
                                ],
                            },
                        ]
                    return 200, {
                        "gcp_project": g_proj,
                        "release_sha": r_sha,
                        "timeSeries": ts_return,
                    }
            elif "dashboards" in url:
                if method == "POST":
                    return 200, {"name": f"projects/{g_proj}/dashboards/platform-health"}
                elif method == "GET":
                    return 200, {
                        "name": f"projects/{g_proj}/dashboards/platform-health",
                        "receipt_id": f"gcp-dash-{test_sha[:12]}",
                        "readback_status": "PROVISIONED",
                        "gcp_project": g_proj,
                        "release_sha": r_sha,
                    }
            return 200, {"status": "ok"}

        exporter = ProductionMetricsExporter(
            release_sha=test_sha,
            registry=registry,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            http_transport=mock_provider_transport,
        )
        exported = exporter.export_metrics()

        has_categories = set(exported.get("categories", [])) >= {
            "latency",
            "error",
            "traffic",
            "job",
            "queue",
            "data",
            "model",
            "business",
            "audit",
        }
        sha_bound = exported.get("release_sha") == test_sha
        has_export_receipt = bool(exported.get("export_receipt_id")) and str(
            exported["export_receipt_id"]
        ).startswith("gcp-cm-readback-")
        has_backend_ids = bool(exported.get("monitoring_backend_resource_ids"))
        readback_success = exported.get("readback_status") == "SUCCESS"
        exporter_ok = (
            has_categories
            and sha_bound
            and has_export_receipt
            and has_backend_ids
            and readback_success
        )

        checks.append(
            CheckResult(
                ok=exporter_ok,
                name="observability:production_metrics_exporter",
                detail=(
                    "ProductionMetricsExporter binds exact 40-char release_sha across categories, invokes provider adapter, and produces Cloud Monitoring backend resource IDs and readback receipt"
                    if exporter_ok
                    else "invalid: ProductionMetricsExporter failed to export bound metrics, backend resource IDs, or readback receipt"
                ),
            )
        )

        provisioned = render_dashboard_provisioning(
            release_sha=test_sha,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            http_transport=mock_provider_transport,
        )
        exact_binding = (
            provisioned.get("release_sha_traceability", {}).get("exact_sha_binding") == test_sha
        )
        has_slo_owner = bool(provisioned.get("release_sha_traceability", {}).get("slo_owner"))
        readback = provisioned.get("provisioning_readback", {})
        readback_ok = (
            readback.get("readback_status") == "PROVISIONED"
            and bool(readback.get("dashboard_resource_ids"))
            and readback.get("provider_route_identity") == monitoring_route
            and readback.get("receipt_id") == f"gcp-dash-{test_sha[:12]}"
        )
        dashboard_ok = exact_binding and has_slo_owner and readback_ok

        checks.append(
            CheckResult(
                ok=dashboard_ok,
                name="observability:dashboard_provisioning",
                detail=(
                    "render_dashboard_provisioning provisions exact release_sha binding, validates SLO owner, invokes provider adapter, and produces dashboard resource IDs readback receipt"
                    if dashboard_ok
                    else "invalid: dashboard provisioning failed to bind release_sha, validate SLO owner, or produce readback receipt"
                ),
            )
        )

        # Fail-closed gate verification: exporter fails closed without config
        fail_closed_unconfigured = False
        try:
            unconfigured_exporter = ProductionMetricsExporter(
                release_sha=test_sha,
                gcp_project="",
                provider_route=monitoring_route,
                http_transport=mock_provider_transport,
            )
            unconfigured_exporter.export_metrics()
        except ValueError:
            fail_closed_unconfigured = True

        # Fail-closed gate verification: exporter fails closed when passing on-call alert route
        fail_closed_oncall_route = False
        try:
            oncall_exporter = ProductionMetricsExporter(
                release_sha=test_sha,
                gcp_project="alfaloop-data-project",
                provider_route="https://oncall-router.oday.plus/api/v1/alerts",
                http_transport=mock_provider_transport,
            )
            oncall_exporter.export_metrics()
        except ValueError as e:
            if "ONCALL_ENDPOINT_URL" in str(e) or "alert route" in str(e):
                fail_closed_oncall_route = True

        # Fail-closed gate verification: exporter fails closed when provider rejects/500
        def rejecting_transport(url: str, payload: dict) -> tuple[int, dict]:
            return 500, {"error": "Internal Server Error"}

        fail_closed_rejection = False
        try:
            rejected_exporter = ProductionMetricsExporter(
                release_sha=test_sha,
                gcp_project="alfaloop-data-project",
                provider_route=monitoring_route,
                http_transport=rejecting_transport,
            )
            rejected_exporter.export_metrics()
        except RuntimeError:
            fail_closed_rejection = True

        # Verify watch window recording with monitoring query execution transport
        from datetime import UTC, datetime, timedelta

        start_time = datetime.now(UTC) - timedelta(minutes=20)
        end_time = datetime.now(UTC)
        watch_receipt = record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_time,
            end_time=end_time,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            query_transport=mock_provider_transport,
        )
        watch_window_ok = watch_receipt.get("status") == "WATCH_PASSED"

        # Verify notification adapter fails closed without ONCALL_ENDPOINT_URL when in production
        notification_fail_closed = False
        try:
            get_notification_adapter(endpoint_url="")
        except ValueError:
            notification_fail_closed = True

        fail_closed_ok = (
            fail_closed_unconfigured
            and fail_closed_oncall_route
            and fail_closed_rejection
            and watch_window_ok
            and notification_fail_closed
        )
        checks.append(
            CheckResult(
                ok=fail_closed_ok,
                name="observability:fail_closed_gates",
                detail=(
                    "Observability exporter, dashboard provisioning, watch-window query execution, and notification adapter fail-closed on missing config, unconfigured route, and provider rejection"
                    if fail_closed_ok
                    else "invalid: observability fail-closed gates failed to reject missing config or provider rejection"
                ),
            )
        )
    except Exception as exc:
        checks.append(
            CheckResult(
                ok=False,
                name="observability:live_wiring",
                detail=f"observability runtime checks failed: {type(exc).__name__}: {exc}",
            )
        )
    return checks


class _ReachableProbeEngine:
    """Production-shaped engine double whose probe queries succeed."""

    is_production = True
    dialect = "postgresql"

    def query(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    def query_one(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ready": 1}


class _UnreachableProbeEngine:
    """Production-shaped engine double whose probe queries fail."""

    is_production = True
    dialect = "postgresql"

    def query(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        raise ConnectionError("preflight probe: database unreachable")

    def query_one(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ConnectionError("preflight probe: database unreachable")


@contextlib.contextmanager
def _environment_overrides(overrides: Mapping[str, str | None]) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in overrides}
    try:
        for name, value in overrides.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _import_runtime_module(root: Path, module_name: str) -> Any:
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    return importlib.import_module(module_name)


# apps.api.oday_api.main builds a module-level default app on import. Importing
# it inside preflight must therefore run under the neutral local composition:
# the deploy environment's live provider mode and PostgreSQL persistence would
# otherwise make the import itself demand live secrets and a reachable
# database. This only neutralizes the import-time side effect - every wiring
# probe below builds its own app from explicit arguments and its own
# environment overrides, and no deployment check is weakened by it.
_MAIN_IMPORT_SAFE_ENVIRONMENT: dict[str, str | None] = {
    "ODP_REQUIRE_LIVE_DATA": None,
    "ODP_DEPLOY_ENV": None,
    "ODAY_ENV": None,
    "ODP_ENV": None,
    "APP_ENV": None,
    "ENVIRONMENT": None,
    "ODP_PRODUCT_MODE": None,
    "NODE_ENV": None,
    "ODP_PERSISTENCE": "memory",
    "ODP_EXTERNAL_PROVIDER_MODE": None,
    "MLFLOW_TRACKING_URI": None,
    "ODP_E2E_MODE": None,
}


def _production_like_bundle(persistence_module: Any, engine: Any) -> Any:
    """A supported-production persistence double for composition probes.

    Mirrors ``replace(_memory_bundle(), mode="postgresql", engine=...)`` from the
    integration suite: the engine advertises ``is_production`` exactly as a real
    ``PostgresEngine`` does, so the probe exercises the same composition gate the
    runtime trusts, without requiring a reachable database inside preflight.
    """

    intake_module = importlib.import_module(
        "shared.infrastructure.persistence.assisted_listing_intake"
    )
    return replace(
        persistence_module._memory_bundle(),
        mode="postgresql",
        engine=engine,
        assisted_intake_store=intake_module.DurableAssistedIntakeStore(
            SimpleNamespace(engine=engine)
        ),
    )


def _classify_bootstrap_payload(payload: Any, *, name: str) -> CheckResult:
    """Distinguish a fail-closed empty response from actual seed exposure."""

    meta = payload.get("meta") if isinstance(payload, Mapping) else None
    meta = meta if isinstance(meta, Mapping) else {}
    origin = meta.get("dataOrigin")
    origin = origin if isinstance(origin, Mapping) else {}
    origin_kind = str(origin.get("kind", "")).strip().lower()
    data_mode = str(meta.get("dataMode", "")).strip().lower()
    row_sections = (
        "workQueue",
        "approvals",
        "notifications",
        "kpis",
        "decisions",
        "riskRows",
        "auditFeed",
    )
    populated = sorted(
        section for section in row_sections if isinstance(payload, Mapping) and payload.get(section)
    )
    if populated or origin_kind in {"fixture", "seed"} or data_mode == "fixture":
        return CheckResult(
            False,
            name,
            "invalid: live-required OperatorStateService exposes seed/fixture data "
            f"(data_mode={data_mode or '<missing>'} origin={origin_kind or '<missing>'} "
            f"populated={','.join(populated) or 'none'})",
        )
    if data_mode == "unavailable" and origin_kind == "unavailable":
        return CheckResult(
            True,
            name,
            "fail-closed: unavailable response with zero fixture rows "
            "(repository unavailable is not seed exposure)",
        )
    return CheckResult(
        False,
        name,
        "unrecognized live-required bootstrap response "
        f"(data_mode={data_mode or '<missing>'} origin={origin_kind or '<missing>'})",
    )


def _operator_absent_repository_check(operator_state_module: Any) -> CheckResult:
    name = OPERATOR_BOOTSTRAP_CHECK
    repository_error = getattr(operator_state_module, "OperatorLiveRepositoryError", None)
    if not isinstance(repository_error, type):
        return CheckResult(False, name, "missing: OperatorLiveRepositoryError contract is absent")
    try:
        service = operator_state_module.OperatorStateService(
            require_live_data=True,
            persistence_mode="memory",
            provider_mode="fixture",
        )
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        return CheckResult(
            False, name, f"operator runtime inspection failed: {type(exc).__name__}: {exc}"
        )
    try:
        payload = service.get_today(role_id="ops-lead")
    except repository_error as exc:
        origin_kind = str(service.data_origin.get("kind", "")).strip().lower()
        fail_closed = origin_kind == "unavailable"
        return CheckResult(
            ok=fail_closed,
            name=name,
            detail=(
                f"fail-closed: absent live repository raises {type(exc).__name__} "
                "with zero fixture rows (repository unavailable is not seed exposure)"
                if fail_closed
                else (
                    "invalid: fail-closed error raised but data origin is "
                    f"{origin_kind or '<missing>'}"
                )
            ),
        )
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        return CheckResult(
            False, name, f"operator runtime inspection failed: {type(exc).__name__}: {exc}"
        )
    return _classify_bootstrap_payload(payload, name=name)


def _operator_missing_tenant_check(
    operator_state_module: Any,
    live_repository_module: Any,
    persistence_module: Any,
) -> CheckResult:
    name = OPERATOR_TENANT_CHECK
    tenant_error = getattr(live_repository_module, "OperatorTenantScopeRequiredError", None)
    if not isinstance(tenant_error, type):
        return CheckResult(
            False, name, "missing: OperatorTenantScopeRequiredError contract is absent"
        )
    try:
        service = operator_state_module.OperatorStateService(
            require_live_data=True,
            persistence_mode="postgresql",
            provider_mode="live",
            live_repository=live_repository_module.OperatorLiveRepository(
                _production_like_bundle(persistence_module, _ReachableProbeEngine())
            ),
        )
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        return CheckResult(
            False, name, f"operator runtime inspection failed: {type(exc).__name__}: {exc}"
        )
    try:
        payload = service.get_today(role_id="ops-lead")
    except tenant_error:
        origin_kind = str(service.data_origin.get("kind", "")).strip().lower()
        no_fixture_rows = origin_kind not in {"fixture", "seed"}
        return CheckResult(
            ok=no_fixture_rows,
            name=name,
            detail=(
                "fail-closed: live read without an authorized tenant raises "
                "OperatorTenantScopeRequiredError with zero fixture rows"
                if no_fixture_rows
                else f"invalid: tenant-scope failure exposed {origin_kind} data origin"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        return CheckResult(
            False,
            name,
            "invalid: missing tenant raised "
            f"{type(exc).__name__} instead of OperatorTenantScopeRequiredError: {exc}",
        )
    classified = _classify_bootstrap_payload(payload, name=name)
    return CheckResult(
        False,
        name,
        "invalid: live read without an authorized tenant returned a payload instead "
        f"of failing closed ({classified.detail})",
    )


def _operator_probe_contract_check(
    live_repository_module: Any,
    persistence_module: Any,
) -> CheckResult:
    name = OPERATOR_PROBE_CHECK
    try:
        reachable_probe = live_repository_module.OperatorLiveRepository(
            _production_like_bundle(persistence_module, _ReachableProbeEngine())
        ).probe()
        unreachable_probe = live_repository_module.OperatorLiveRepository(
            _production_like_bundle(persistence_module, _UnreachableProbeEngine())
        ).probe()
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        return CheckResult(
            False, name, f"live repository probe inspection failed: {type(exc).__name__}: {exc}"
        )
    ok = (
        bool(reachable_probe.ready)
        and not reachable_probe.errors
        and str(getattr(reachable_probe, "repository", "")) == "OperatorLiveRepository"
        and str(getattr(reachable_probe, "persistence_mode", "")).strip().lower()
        in POSTGRES_PERSISTENCE_MODES
        and not unreachable_probe.ready
        and bool(unreachable_probe.errors)
    )
    return CheckResult(
        ok=ok,
        name=name,
        detail=(
            "live repository probe is ready only when the database is reachable "
            "and carries repository/persistence provenance"
            if ok
            else (
                "invalid: probe does not distinguish reachable from unreachable "
                f"repository state (ready={reachable_probe.ready} "
                f"unreachable_ready={unreachable_probe.ready})"
            )
        ),
    )


def _operator_create_app_wiring_checks(
    main_module: Any,
    persistence_module: Any,
) -> list[CheckResult]:
    create_app = getattr(main_module, "create_app", None)
    if not callable(create_app):
        return [
            CheckResult(
                False,
                OPERATOR_WIRING_CHECK,
                "create_app is unavailable; cannot verify production operator composition",
            )
        ]
    # The provider stub only isolates this composition probe from provider
    # configuration, which preflight validates separately and fail-closed.
    # MLFLOW_TRACKING_URI is removed so the model-binding path fails fast and
    # offline; model readiness has its own gate and stays blocked either way.
    probe_environment: dict[str, str | None] = {
        "ODP_REQUIRE_LIVE_DATA": "true",
        "MLFLOW_TRACKING_URI": None,
        "ODP_E2E_MODE": None,
    }
    provider_validation = SimpleNamespace(ok=True, errors=(), mode="live")
    try:
        memory_bundle = persistence_module._memory_bundle()
        production_bundle = _production_like_bundle(persistence_module, _ReachableProbeEngine())
        with _environment_overrides({**probe_environment, "ODP_PERSISTENCE": "postgresql"}):
            production_app = create_app(
                persistence=production_bundle,
                external_provider_validation=provider_validation,
            )
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        return [
            CheckResult(
                False,
                OPERATOR_WIRING_CHECK,
                f"production create_app composition failed: {type(exc).__name__}: {exc}",
            )
        ]

    repository = getattr(
        getattr(production_app, "state", SimpleNamespace()),
        "operator_live_repository",
        None,
    )
    origin = getattr(repository, "data_origin", None)
    origin = origin if isinstance(origin, Mapping) else {}
    probe = None
    if repository is not None and callable(getattr(repository, "probe", None)):
        try:
            probe = repository.probe()
        except Exception:  # noqa: BLE001 - a failing probe is reported below
            probe = None
    wired = (
        repository is not None
        and type(repository).__name__ == "OperatorLiveRepository"
        and str(origin.get("kind", "")).strip().lower() == "authoritative"
        and str(origin.get("persistenceMode", "")).strip().lower() in POSTGRES_PERSISTENCE_MODES
        and probe is not None
        and bool(probe.ready)
    )
    checks = [
        CheckResult(
            ok=wired,
            name=OPERATOR_WIRING_CHECK,
            detail=(
                "production PostgreSQL create_app injects OperatorLiveRepository "
                "with authoritative provenance and a passing live probe"
                if wired
                else (
                    "missing: production create_app did not inject a ready "
                    "OperatorLiveRepository (repository="
                    f"{type(repository).__name__ if repository is not None else None} "
                    f"origin_kind={origin.get('kind') or '<missing>'} "
                    f"probe_ready={getattr(probe, 'ready', None)})"
                )
            ),
        )
    ]

    try:
        with _environment_overrides({**probe_environment, "ODP_PERSISTENCE": "memory"}):
            fixture_app = create_app(
                persistence=memory_bundle,
                external_provider_validation=provider_validation,
            )
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        checks.append(
            CheckResult(
                False,
                OPERATOR_FIXTURE_WIRING_CHECK,
                f"memory-mode create_app inspection failed: {type(exc).__name__}: {exc}",
            )
        )
        return checks
    fixture_repository = getattr(
        getattr(fixture_app, "state", SimpleNamespace()),
        "operator_live_repository",
        "<unset>",
    )
    blocked = fixture_repository is None
    checks.append(
        CheckResult(
            ok=blocked,
            name=OPERATOR_FIXTURE_WIRING_CHECK,
            detail=(
                "non-production persistence gets no live operator repository and stays fail-closed"
                if blocked
                else ("invalid: fixture/seed persistence received an operator repository binding")
            ),
        )
    )
    return checks


_OPERATOR_RUNTIME_CHECK_CACHE: dict[str, tuple[CheckResult, ...]] = {}


def operator_runtime_checks(
    root: Path = ROOT,
    *,
    main_module: Any | None = None,
    operator_state_module: Any | None = None,
    live_repository_module: Any | None = None,
    persistence_module: Any | None = None,
) -> list[CheckResult]:
    """Behavioral operator runtime composition checks.

    Executes the real modules instead of scanning source markers so the
    preflight can (1) prove the production PostgreSQL ``create_app``
    composition injects ``OperatorLiveRepository``, (2) prove an absent
    repository or missing tenant fails closed with zero fixture rows, and
    (3) still block actual seed/fixture exposure - without conflating a
    correctly fail-closed unavailable repository with seed exposure.
    """

    injected = any(
        module is not None
        for module in (
            main_module,
            operator_state_module,
            live_repository_module,
            persistence_module,
        )
    )
    cache_key = str(root.resolve())
    if not injected and cache_key in _OPERATOR_RUNTIME_CHECK_CACHE:
        return list(_OPERATOR_RUNTIME_CHECK_CACHE[cache_key])

    try:
        operator_state_module = operator_state_module or _import_runtime_module(
            root, "modules.opsboard.application.operator_state"
        )
        live_repository_module = live_repository_module or _import_runtime_module(
            root, "modules.opsboard.application.operator_live_repository"
        )
        persistence_module = persistence_module or _import_runtime_module(
            root, "shared.infrastructure.persistence.factory"
        )
        if main_module is None:
            with _environment_overrides(_MAIN_IMPORT_SAFE_ENVIRONMENT):
                main_module = _import_runtime_module(root, "apps.api.oday_api.main")
    except Exception as exc:  # noqa: BLE001 - failed inspection must block deployment
        return [
            CheckResult(
                False,
                OPERATOR_BOOTSTRAP_CHECK,
                f"cannot import operator runtime for inspection: {type(exc).__name__}: {exc}",
            )
        ]

    checks = [
        _operator_absent_repository_check(operator_state_module),
        _operator_missing_tenant_check(
            operator_state_module, live_repository_module, persistence_module
        ),
        _operator_probe_contract_check(live_repository_module, persistence_module),
    ]
    checks.extend(_operator_create_app_wiring_checks(main_module, persistence_module))
    if not injected:
        _OPERATOR_RUNTIME_CHECK_CACHE[cache_key] = tuple(checks)
    return checks


def _provider_definitions(root: Path) -> tuple[Any, ...]:
    """Import and return the provider registry definitions."""

    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    registry_module = importlib.import_module("modules.external_data.connectors.provider_registry")
    return tuple(registry_module.provider_registry())


def provider_allowlist_checks(
    *,
    env: Mapping[str, str],
    root: Path = ROOT,
) -> tuple[list[CheckResult], frozenset[str]]:
    """Validate the explicit production provider allowlist against licenses."""

    raw_ids = env.get(PRODUCTION_PROVIDER_IDS_ENV, "")
    selected_ids = frozenset(
        provider_id.strip() for provider_id in raw_ids.split(",") if provider_id.strip()
    )
    checks = [
        CheckResult(
            bool(selected_ids),
            f"runtime:{PRODUCTION_PROVIDER_IDS_ENV}",
            (
                f"selected={','.join(sorted(selected_ids))}"
                if selected_ids
                else "missing explicit production provider allowlist"
            ),
        )
    ]

    try:
        providers = _provider_definitions(root)
    except Exception as exc:  # noqa: BLE001 - preflight must report import failures
        checks.append(
            CheckResult(
                False,
                "repository:provider_registry_import",
                f"cannot import provider registry: {type(exc).__name__}: {exc}",
            )
        )
        return checks, selected_ids

    by_id = {provider.provider_id: provider for provider in providers}
    unknown_ids = selected_ids - by_id.keys()
    missing_ids = REQUIRED_PRODUCT_PROVIDER_IDS - selected_ids
    license_blocked_ids = frozenset(
        provider_id
        for provider_id in selected_ids & by_id.keys()
        if not by_id[provider_id].license.allowed_in_production
    )
    checks.extend(
        [
            CheckResult(
                not unknown_ids,
                "runtime:production_provider_ids_known",
                (
                    "all selected provider IDs exist"
                    if not unknown_ids
                    else f"unknown={','.join(sorted(unknown_ids))}"
                ),
            ),
            CheckResult(
                not missing_ids,
                "runtime:required_product_providers",
                (
                    "all required product providers selected"
                    if not missing_ids
                    else f"missing={','.join(sorted(missing_ids))}"
                ),
            ),
            CheckResult(
                not license_blocked_ids,
                "runtime:production_provider_licenses",
                (
                    "all selected providers are production-enabled"
                    if not license_blocked_ids
                    else f"license_blocked={','.join(sorted(license_blocked_ids))}"
                ),
            ),
            CheckResult(
                "competitor.manual_source" not in selected_ids,
                "runtime:competitor_manual_disabled",
                (
                    "competitor.manual_source is disabled for production"
                    if "competitor.manual_source" not in selected_ids
                    else "competitor.manual_source must not be in the production allowlist"
                ),
            ),
        ]
    )
    production_enabled_ids = frozenset(
        provider_id
        for provider_id in selected_ids & by_id.keys()
        if by_id[provider_id].license.allowed_in_production
    )
    return checks, production_enabled_ids


def provider_adapter_checks(
    root: Path = ROOT,
    *,
    production_provider_ids: frozenset[str] = REQUIRED_PRODUCT_PROVIDER_IDS,
) -> list[CheckResult]:
    """Import concrete adapters for production-enabled selected providers."""

    try:
        providers = _provider_definitions(root)
    except Exception as exc:  # noqa: BLE001 - preflight must report import failures
        return [
            CheckResult(
                False,
                "repository:provider_registry_import",
                f"cannot import provider registry: {type(exc).__name__}: {exc}",
            )
        ]

    checks: list[CheckResult] = []
    for provider in providers:
        if provider.provider_id not in production_provider_ids:
            continue
        module_name, separator, class_name = provider.provider_class.rpartition(".")
        if not separator:
            checks.append(
                CheckResult(
                    False,
                    f"repository:provider_adapter:{provider.provider_id}",
                    f"invalid provider_class={provider.provider_class}",
                )
            )
            continue
        try:
            module = importlib.import_module(module_name)
            adapter = getattr(module, class_name)
            ok = inspect.isclass(adapter)
            detail = (
                f"importable class={provider.provider_class}"
                if ok
                else f"provider_class is not a class: {provider.provider_class}"
            )
        except Exception as exc:  # noqa: BLE001 - any import-time failure blocks deploy
            ok = False
            detail = (
                f"missing concrete adapter class={provider.provider_class} "
                f"({type(exc).__name__}: {exc})"
            )
        checks.append(
            CheckResult(
                ok,
                f"repository:provider_adapter:{provider.provider_id}",
                detail,
            )
        )
    return checks


def selected_provider_config_checks(
    *,
    env: Mapping[str, str],
    production_provider_ids: frozenset[str],
    root: Path = ROOT,
) -> list[CheckResult]:
    """Require endpoint/auth/secret configuration only for selected providers."""

    try:
        providers = _provider_definitions(root)
    except Exception as exc:  # noqa: BLE001 - registry import is reported fail-closed
        return [
            CheckResult(
                False,
                "repository:provider_registry_import",
                f"cannot import provider registry: {type(exc).__name__}: {exc}",
            )
        ]

    checks: list[CheckResult] = []
    for provider in providers:
        if provider.provider_id not in production_provider_ids:
            continue
        names = []
        if provider.endpoint_env_var:
            names.append(("config", provider.endpoint_env_var))
        for credential in provider.credentials:
            if not credential.required_in_live:
                continue
            names.append(("secret-reference", f"{credential.env_var}_SECRET"))
            if credential.status_env_var:
                names.append(("config", credential.status_env_var))
        for kind, name in names:
            configured = _configured(env.get(name, ""))
            checks.append(
                CheckResult(
                    configured,
                    f"{kind}:{name}",
                    (
                        "configured (value redacted)"
                        if kind == "secret-reference" and configured
                        else "configured"
                        if configured
                        else "missing or placeholder"
                    ),
                )
            )
    return checks


def preflight_checks(
    *,
    env: Mapping[str, str],
    expected_environment: str,
    expected_sha: str,
    root: Path = ROOT,
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    for name in REQUIRED_PUBLIC_CONFIG:
        checks.append(
            CheckResult(
                ok=_configured(env.get(name, "")),
                name=f"config:{name}",
                detail="configured" if _configured(env.get(name, "")) else "missing or placeholder",
            )
        )

    for name in REQUIRED_SECRET_REFERENCES:
        checks.append(
            CheckResult(
                ok=_configured(env.get(name, "")),
                name=f"secret-reference:{name}",
                detail=(
                    "configured (value redacted)"
                    if _configured(env.get(name, ""))
                    else "missing or placeholder"
                ),
            )
        )

    for name in REQUIRED_SECRET_VALUES:
        checks.append(
            CheckResult(
                ok=bool(env.get(name, "").strip()),
                name=f"secret:{name}",
                detail="configured (value redacted)" if env.get(name, "").strip() else "missing",
            )
        )

    actual_environment = env.get("ODP_DEPLOY_ENV", "").strip().lower()
    checks.append(
        CheckResult(
            ok=actual_environment == expected_environment.strip().lower(),
            name="runtime:ODP_DEPLOY_ENV",
            detail=(
                f"expected={expected_environment.strip().lower()} "
                f"actual={actual_environment or '<missing>'}"
            ),
        )
    )

    for name, expected in REQUIRED_RUNTIME_VALUES.items():
        actual = env.get(name, "").strip().lower()
        checks.append(
            CheckResult(
                ok=actual == expected,
                name=f"runtime:{name}",
                detail=f"expected={expected} actual={actual or '<missing>'}",
            )
        )

    checks.append(_bounded_provider_probe_timeout_check(env))

    forecast_binding = (
        env.get("ODP_FORECAST_ENGINE", "").strip().lower(),
        env.get("ODP_FORECAST_MODEL", "").strip().lower(),
    )
    checks.append(
        CheckResult(
            ok=forecast_binding in SUPPORTED_FORECAST_BINDINGS,
            name="runtime:forecast_binding",
            detail=(
                f"supported={forecast_binding[0]}:{forecast_binding[1]}"
                if forecast_binding in SUPPORTED_FORECAST_BINDINGS
                else f"unsupported={forecast_binding[0] or '<missing>'}:{forecast_binding[1] or '<missing>'}"
            ),
        )
    )

    normalized_sha = expected_sha.strip().lower()
    checks.append(
        CheckResult(
            ok=bool(SHA_PATTERN.fullmatch(normalized_sha))
            and env.get("ODAY_RELEASE_SHA", "").strip().lower() == normalized_sha,
            name="runtime:ODAY_RELEASE_SHA",
            detail=(
                "valid 40-character release SHA"
                if bool(SHA_PATTERN.fullmatch(normalized_sha))
                and env.get("ODAY_RELEASE_SHA", "").strip().lower() == normalized_sha
                else "must be the expected 40-character lowercase Git SHA"
            ),
        )
    )

    allowlist_checks, production_provider_ids = provider_allowlist_checks(
        env=env,
        root=root,
    )
    checks.extend(allowlist_checks)
    checks.extend(
        selected_provider_config_checks(
            env=env,
            production_provider_ids=production_provider_ids,
            root=root,
        )
    )
    checks.extend(
        repository_capability_checks(
            root,
            production_provider_ids=production_provider_ids,
        )
    )
    return checks


def _request(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int, str, str]:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return (
                response.status,
                response.headers.get("content-type", ""),
                response.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            exc.headers.get("content-type", ""),
            exc.read().decode("utf-8", errors="replace"),
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _request_without_redirect(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int, str | None]:
    request = urllib.request.Request(url, headers=dict(headers))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.headers.get("location")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("location")


def _effective_port(parsed: urllib.parse.ParseResult) -> int | None:
    try:
        if parsed.port is not None:
            return parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _is_safe_protected_redirect(
    web_url: str,
    web_status: int,
    location: str | None,
    *,
    protected_path: str = "/operator",
    target_path: str = "/login",
) -> bool:
    if (
        web_status not in {302, 303, 307, 308}
        or not isinstance(location, str)
        or not location.strip()
    ):
        return False

    try:
        raw_location = location.strip()
        request_url = f"{web_url.rstrip('/')}{protected_path}"
        base_parsed = urllib.parse.urlparse(request_url)
        resolved_url = urllib.parse.urljoin(request_url, raw_location)
        target_parsed = urllib.parse.urlparse(resolved_url)

        # Reject userinfo / credentials in target URL
        if target_parsed.username or target_parsed.password or "@" in target_parsed.netloc:
            return False

        # Reject fragments in target URL
        if target_parsed.fragment:
            return False

        # Scheme must match base scheme (reject scheme downgrade, e.g. https -> http)
        base_scheme = base_parsed.scheme.lower()
        target_scheme = target_parsed.scheme.lower()
        if not base_scheme or base_scheme != target_scheme:
            return False

        # Hostname must match normalized base hostname
        base_host = (base_parsed.hostname or "").lower()
        target_host = (target_parsed.hostname or "").lower()
        if not base_host or base_host != target_host:
            return False

        # Effective port must match (including default vs nondefault port mismatches)
        base_port = _effective_port(base_parsed)
        target_port = _effective_port(target_parsed)
        if base_port is None or target_port is None or base_port != target_port:
            return False

        # Path must match expected target_path (e.g. /login)
        if target_parsed.path != target_path:
            return False

        # returnTo parameter (parse_qs already URL-decodes values once; avoid double-decoding)
        query_params = urllib.parse.parse_qs(target_parsed.query, keep_blank_values=True)
        return_to_list = query_params.get("returnTo")
        if not return_to_list or len(return_to_list) != 1:
            return False

        if return_to_list[0] != protected_path:
            return False

        return True
    except ValueError:
        return False


def _is_plain_relative_path(value: str) -> bool:
    """True when a decoded parameter value is a bare same-origin path."""

    return (
        value.startswith("/")
        and not value.startswith("//")
        and "://" not in value
        and all(ch.isprintable() for ch in value)
    )


def _redact_location(location: str | None) -> str:
    """Render a Location header for reports without echoing secret material.

    Redirect targets can carry credentials in userinfo or session/bearer values
    in query parameters, so the raw header must never reach a report that
    advertises ``secret_values_redacted``. The structure the protected-redirect
    contract is judged on (scheme, host, effective port, path, parameter names)
    is preserved; every parameter value is masked except ``returnTo``, which is
    kept only when it decodes to a bare same-origin path.
    """

    if not isinstance(location, str) or not location.strip():
        return "<missing>"

    raw = location.strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = f"{parsed.scheme.lower()}:" if parsed.scheme else ""

        netloc = ""
        if parsed.netloc:
            host = (parsed.hostname or "<invalid-host>").lower()
            try:
                port = f":{parsed.port}" if parsed.port is not None else ""
            except ValueError:
                port = ":<invalid-port>"
            userinfo = "<redacted>@" if "@" in parsed.netloc else ""
            netloc = f"//{userinfo}{host}{port}"

        query = ""
        if parsed.query:
            pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not pairs:
                query = "?<redacted>"
            else:
                rendered = []
                for key, value in pairs:
                    if key == "returnTo" and _is_plain_relative_path(value):
                        rendered.append(f"{key}={urllib.parse.quote(value, safe='')}")
                    else:
                        rendered.append(f"{key}=<redacted>")
                query = "?" + "&".join(rendered)

        fragment = "#<redacted>" if parsed.fragment else ""
        return f"{scheme}{netloc}{parsed.path}{query}{fragment}"
    except ValueError:
        return "<unparsable>"


def _json_request(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    status, _, body = _request(url, headers=headers, timeout=timeout)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{url} did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{url} returned a non-object JSON payload")
    return status, payload


def _declared_data_mode(payload: Mapping[str, Any]) -> str:
    containers: list[Mapping[str, Any]] = [payload]
    for key in ("details", "dependencies", "meta"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    for container in containers:
        for key in ("data_mode", "dataMode", "binding_mode", "bindingMode"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return ""


def _dependency_text(payload: Mapping[str, Any], key: str) -> str:
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, Mapping):
        dependencies = payload.get("details")
    if not isinstance(dependencies, Mapping):
        return ""
    value = dependencies.get(key)
    return json.dumps(value, sort_keys=True).lower() if value is not None else ""


def _provider_probe_checks(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[CheckResult]:
    dependencies = payload.get("dependencies")
    provider_report = (
        dependencies.get("external_providers") if isinstance(dependencies, Mapping) else None
    )
    if not isinstance(provider_report, Mapping):
        return [
            CheckResult(
                False,
                "smoke:/platform/health:external_providers:contract",
                "missing structured provider connectivity report",
            )
        ]

    connectivity_ok = (
        provider_report.get("status") == "healthy"
        and provider_report.get("mode") == "live"
        and provider_report.get("configuration_valid") is True
        and provider_report.get("connectivity_healthy") is True
    )
    checks = [
        CheckResult(
            ok=connectivity_ok,
            name="smoke:/platform/health:external_providers:connectivity",
            detail=(
                "configuration and live connectivity healthy"
                if connectivity_ok
                else "configuration or live connectivity is not healthy"
            ),
        )
    ]
    declared_ids = {
        str(value)
        for value in provider_report.get("required_provider_ids", [])
        if isinstance(value, str)
    }
    probes = provider_report.get("probes")
    probe_items = probes if isinstance(probes, list) else []
    probe_by_id = {
        str(probe.get("provider_id")): probe
        for probe in probe_items
        if isinstance(probe, Mapping) and isinstance(probe.get("provider_id"), str)
    }
    complete = (
        declared_ids == REQUIRED_PRODUCT_PROVIDER_IDS
        and set(probe_by_id) == REQUIRED_PRODUCT_PROVIDER_IDS
    )
    checks.append(
        CheckResult(
            ok=complete,
            name="smoke:/platform/health:external_providers:completeness",
            detail=(
                "all required providers have probe evidence"
                if complete
                else (
                    "missing provider probe evidence: "
                    + ",".join(sorted(REQUIRED_PRODUCT_PROVIDER_IDS - set(probe_by_id)))
                )
            ),
        )
    )

    checked_at = _parse_utc_timestamp(provider_report.get("checked_at"))
    expires_at = _parse_utc_timestamp(provider_report.get("expires_at"))
    freshness_ok = _probe_timestamp_is_fresh(
        checked_at=checked_at,
        expires_at=expires_at,
        now=now,
    )
    checks.append(
        CheckResult(
            ok=freshness_ok,
            name="smoke:/platform/health:external_providers:freshness",
            detail=(
                "provider probe report is fresh"
                if freshness_ok
                else "provider probe report is missing, expired, or too old"
            ),
        )
    )

    for provider_id in sorted(REQUIRED_PRODUCT_PROVIDER_IDS):
        evidence = probe_by_id.get(provider_id)
        evidence_checked_at = (
            _parse_utc_timestamp(evidence.get("checked_at")) if evidence is not None else None
        )
        evidence_expires_at = (
            _parse_utc_timestamp(evidence.get("expires_at")) if evidence is not None else None
        )
        evidence_ok = bool(
            evidence is not None
            and evidence.get("configuration_valid") is True
            and evidence.get("connectivity_healthy") is True
            and evidence.get("authentication_accepted") is True
            and evidence.get("response_valid") is True
            and evidence.get("schema_valid") is True
            and evidence.get("http_status") == 200
            and evidence.get("reason_code") == "ok"
            and _probe_timestamp_is_fresh(
                checked_at=evidence_checked_at,
                expires_at=evidence_expires_at,
                now=now,
            )
        )
        reason = (
            str(evidence.get("reason_code") or "missing_reason")
            if evidence is not None
            else "missing_probe"
        )
        checks.append(
            CheckResult(
                ok=evidence_ok,
                name=(f"smoke:/platform/health:external_providers:{provider_id}"),
                detail=(
                    "authenticated response and schema probe passed"
                    if evidence_ok
                    else f"provider probe failed: reason_code={reason}"
                ),
            )
        )
    return checks


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _probe_timestamp_is_fresh(
    *,
    checked_at: datetime | None,
    expires_at: datetime | None,
    now: datetime | None,
) -> bool:
    if checked_at is None or expires_at is None:
        return False
    resolved_now = (now or datetime.now(UTC)).astimezone(UTC)
    age_seconds = (resolved_now - checked_at).total_seconds()
    return (
        -5 <= age_seconds <= MAX_PROVIDER_PROBE_AGE_SECONDS
        and checked_at <= expires_at
        and resolved_now <= expires_at
    )


def _contains_forbidden_marker(value: str) -> bool:
    normalized = value.lower()
    return any(marker in normalized for marker in FORBIDDEN_DATA_MARKERS)


def _operator_readiness_check(payload: Mapping[str, Any]) -> CheckResult:
    """Require a passing live repository probe with read provenance."""

    details = payload.get("details")
    data_section = details.get("data") if isinstance(details, Mapping) else None
    data_section = data_section if isinstance(data_section, Mapping) else {}
    probe = data_section.get("operatorRepositoryProbe")
    probe = probe if isinstance(probe, Mapping) else {}
    origin = data_section.get("origin")
    origin = origin if isinstance(origin, Mapping) else {}
    origin_text = json.dumps(origin, sort_keys=True).lower()
    probe_text = json.dumps(probe, sort_keys=True).lower()
    ok = (
        data_section.get("operatorRepositoryReady") is True
        and probe.get("ready") is True
        and not probe.get("errors")
        and str(probe.get("persistenceMode", "")).strip().lower() in POSTGRES_PERSISTENCE_MODES
        and str(origin.get("kind", "")).strip().lower() == "authoritative"
        and bool(str(origin.get("sourceId") or "").strip())
        and not _contains_forbidden_marker(origin_text)
        and not _contains_forbidden_marker(probe_text)
    )
    return CheckResult(
        ok=ok,
        name="smoke:/readiness:operator_live_repository",
        detail=(
            "live operator repository probe passed with authoritative read provenance"
            if ok
            else (
                "missing or failing operator repository probe/provenance: "
                f"ready={data_section.get('operatorRepositoryReady')} "
                f"probe_ready={probe.get('ready')} "
                f"origin_kind={origin.get('kind') or '<missing>'}"
            )
        ),
    )


def _operator_source(payload: Mapping[str, Any]) -> str:
    meta = payload.get("meta")
    containers = [payload, meta] if isinstance(meta, Mapping) else [payload]
    for container in containers:
        for key in ("data_source", "dataSource", "source", "origin"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return ""


def smoke_checks(
    *,
    api_url: str,
    web_url: str,
    expected_sha: str | None,
    bearer_token: str,
    operator_role: str,
    operator_subject: str,
    operator_tenant: str,
    correlation_id: str,
    timeout: float,
) -> tuple[list[CheckResult], dict[str, Any]]:
    checks: list[CheckResult] = []
    report: dict[str, Any] = {
        "api_url": api_url.rstrip("/"),
        "web_url": web_url.rstrip("/"),
        "expected_sha": expected_sha,
        "correlation_id": correlation_id,
        "secret_values_redacted": True,
    }
    base_headers = {"x-correlation-id": correlation_id}

    probes = (
        ("version", "/platform/version"),
        ("health", "/platform/health"),
        ("readiness", "/readiness"),
    )
    payloads: dict[str, dict[str, Any]] = {}
    for name, path in probes:
        try:
            status, payload = _json_request(
                f"{api_url.rstrip('/')}{path}",
                headers=base_headers,
                timeout=timeout,
            )
            payloads[name] = payload
            report[name] = payload
            checks.append(
                CheckResult(
                    ok=status == 200,
                    name=f"smoke:{path}:http",
                    detail=f"status={status}",
                )
            )
        except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
            checks.append(CheckResult(False, f"smoke:{path}:http", str(exc)))

    if expected_sha is not None:
        version = payloads.get("version", {})
        actual_sha = str(version.get("release_sha") or "").strip().lower()
        checks.append(
            CheckResult(
                ok=actual_sha == expected_sha.strip().lower(),
                name="smoke:/platform/version:release_sha",
                detail=(
                    f"expected={expected_sha.strip().lower()} actual={actual_sha or '<missing>'}"
                ),
            )
        )

    for name in ("health", "readiness"):
        payload = payloads.get(name, {})
        path = "/platform/health" if name == "health" else "/readiness"
        data_mode = _declared_data_mode(payload)
        checks.append(
            CheckResult(
                ok=payload.get("status") == "ok" and data_mode == "live",
                name=f"smoke:{path}:live_data_mode",
                detail=f"status={payload.get('status')} data_mode={data_mode or '<missing>'}",
            )
        )
        database = _dependency_text(payload, "database")
        checks.append(
            CheckResult(
                ok=bool(database) and not _contains_forbidden_marker(database),
                name=f"smoke:{path}:database",
                detail=(
                    "non-memory database reported"
                    if database and not _contains_forbidden_marker(database)
                    else "missing or memory/SQLite database reported"
                ),
            )
        )

    health = payloads.get("health", {})
    checks.extend(_provider_probe_checks(health))
    checks.append(_operator_readiness_check(payloads.get("readiness", {})))
    job_queue = _dependency_text(health, "job_queue")
    checks.append(
        CheckResult(
            ok=bool(job_queue)
            and "healthy" in job_queue
            and not _contains_forbidden_marker(job_queue)
            and any(marker in job_queue for marker in ("worker", "cloud", "durable")),
            name="smoke:/platform/health:job_queue",
            detail=(
                "external worker queue healthy"
                if job_queue
                and "healthy" in job_queue
                and not _contains_forbidden_marker(job_queue)
                and any(marker in job_queue for marker in ("worker", "cloud", "durable"))
                else "missing or non-worker/in-memory job queue"
            ),
        )
    )

    operator_headers = {
        **base_headers,
        "authorization": f"Bearer {bearer_token}",
        "x-operator-role": operator_role,
    }
    try:
        status, bootstrap = _json_request(
            f"{api_url.rstrip('/')}/api/v1/operator/bootstrap",
            headers=operator_headers,
            timeout=timeout,
        )
        report["operator_bootstrap"] = {
            "status": status,
            "data_mode": _declared_data_mode(bootstrap) or None,
            "data_source": _operator_source(bootstrap) or None,
        }
        checks.append(
            CheckResult(
                ok=status == 200,
                name="smoke:/api/v1/operator/bootstrap:http",
                detail=f"status={status}",
            )
        )
        bootstrap_mode = _declared_data_mode(bootstrap)
        bootstrap_source = _operator_source(bootstrap)
        checks.append(
            CheckResult(
                ok=bootstrap_mode == "live"
                and bool(bootstrap_source)
                and not _contains_forbidden_marker(bootstrap_source),
                name="smoke:/api/v1/operator/bootstrap:provenance",
                detail=(
                    f"data_mode={bootstrap_mode or '<missing>'} "
                    f"data_source={bootstrap_source or '<missing>'}"
                ),
            )
        )
        bootstrap_meta = bootstrap.get("meta")
        bootstrap_meta = bootstrap_meta if isinstance(bootstrap_meta, Mapping) else {}
        bootstrap_origin = bootstrap_meta.get("dataOrigin")
        bootstrap_origin = bootstrap_origin if isinstance(bootstrap_origin, Mapping) else {}
        live_readiness = bootstrap_meta.get("liveReadiness")
        live_readiness = live_readiness if isinstance(live_readiness, Mapping) else {}
        bootstrap_origin_text = json.dumps(bootstrap_origin, sort_keys=True).lower()
        report["operator_bootstrap"]["origin_kind"] = (
            str(bootstrap_origin.get("kind") or "") or None
        )
        provenance_ok = (
            str(bootstrap_origin.get("kind", "")).strip().lower() == "authoritative"
            and str(bootstrap_origin.get("persistenceMode", "")).strip().lower()
            in POSTGRES_PERSISTENCE_MODES
            and bool(str(bootstrap_origin.get("sourceId") or "").strip())
            and live_readiness.get("ready") is True
            and not _contains_forbidden_marker(bootstrap_origin_text)
        )
        checks.append(
            CheckResult(
                ok=provenance_ok,
                name="smoke:/api/v1/operator/bootstrap:read_provenance",
                detail=(
                    "authoritative live repository read provenance verified"
                    if provenance_ok
                    else (
                        f"origin_kind={bootstrap_origin.get('kind') or '<missing>'} "
                        "persistence_mode="
                        f"{bootstrap_origin.get('persistenceMode') or '<missing>'} "
                        f"live_ready={live_readiness.get('ready')}"
                    )
                ),
            )
        )
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        checks.append(CheckResult(False, "smoke:/api/v1/operator/bootstrap:http", str(exc)))

    try:
        web_status, location = _request_without_redirect(
            f"{web_url.rstrip('/')}/operator",
            headers=base_headers,
            timeout=timeout,
        )
        auth_redirect = _is_safe_protected_redirect(web_url, web_status, location)
        redacted_location = _redact_location(location)
        report["web_operator_redirect"] = {
            "status": web_status,
            "location_redacted": redacted_location,
            "protected_redirect": auth_redirect,
        }
        checks.append(
            CheckResult(
                ok=auth_redirect,
                name="smoke:web:/operator",
                detail=(
                    f"status={web_status} protected_redirect={str(auth_redirect).lower()} "
                    f"location={redacted_location}"
                ),
            )
        )
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        checks.append(CheckResult(False, "smoke:web:/operator", str(exc)))

    return checks, report


# The migration compatibility gate is the first request the deployment sends to
# the *old* revision. Dev/staging revisions carry no `minScale` annotation and
# receive no organic traffic between deployments, so that request reliably pays
# a Cloud Run cold start: run 30402570022 measured 28.1s of first-byte latency
# against a single 15.0s attempt and failed the gate even though the old
# revision answered `/platform/version` with 200 and reported a healthy
# database. Retrying is therefore about outlasting instance startup, never
# about tolerating an answer we dislike -- see probe_failure_is_transient.
COMPATIBILITY_PROBE_ATTEMPTS = 4
COMPATIBILITY_PROBE_BACKOFF_SECONDS = 2.0
COMPATIBILITY_PROBE_MAX_BACKOFF_SECONDS = 8.0
COMPATIBILITY_PROBE_DEADLINE_SECONDS = 120.0
# Where a probe attempt stopped. This -- not `payload is None` -- is what
# decides retryability, because `payload is None` is equally true of a request
# that never reached the old revision and of a response the old revision sent
# with a body we could not parse.
PROBE_NO_RESPONSE = "no_response"  # transport failure or timeout: nothing came back
PROBE_INVALID_REQUEST = "invalid_request"  # the request could not be built: a defective URL
PROBE_UNPARSEABLE_BODY = "unparseable_body"  # a response arrived; its body was not JSON
PROBE_NON_OBJECT_BODY = "non_object_body"  # a response arrived; its JSON was not an object
PROBE_JSON_OBJECT = "json_object"  # a response arrived with a JSON object body
PROBE_PROVENANCES = frozenset(
    {
        PROBE_NO_RESPONSE,
        PROBE_INVALID_REQUEST,
        PROBE_UNPARSEABLE_BODY,
        PROBE_NON_OBJECT_BODY,
        PROBE_JSON_OBJECT,
    }
)
# Nothing came back, so there is no status to record. These two differ only in
# whether a retry could ever change the answer: a cold start can be outlasted, a
# URL we cannot turn into a request cannot.
PROBE_NO_STATUS_PROVENANCES = frozenset({PROBE_NO_RESPONSE, PROBE_INVALID_REQUEST})
# Positive allowlist rather than a substring probe: `"unhealthy" in text` is
# true for the literal dependency value `"unhealthy"`, so a substring test
# would pass the very verdict this gate exists to catch.
HEALTHY_DATABASE_STATUSES = frozenset({"healthy", "ok", "up", "pass", "passed"})


def _database_status_token(health: Mapping[str, Any]) -> str:
    """Return the old revision's declared database status, or '' when absent."""

    dependencies = health.get("dependencies")
    if not isinstance(dependencies, Mapping):
        dependencies = health.get("details")
    if not isinstance(dependencies, Mapping):
        return ""
    value = dependencies.get("database")
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, Mapping):
        for key in ("status", "state", "health"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip().lower()
    return ""


def _database_reads_healthy(health: Mapping[str, Any]) -> bool:
    """Fail closed unless the database dependency explicitly reads healthy."""

    token = _database_status_token(health)
    if token not in HEALTHY_DATABASE_STATUSES:
        return False
    return not _contains_forbidden_marker(_dependency_text(health, "database"))


@dataclass(frozen=True)
class ProbeAttempt:
    """One request to an old-revision probe endpoint. Never raises.

    ``provenance`` is mandatory and self-consistent by construction: a status
    is present exactly when a response was received, and a payload exactly
    when that response's body parsed as a JSON object. Callers therefore
    cannot silently classify a received-but-unreadable response as a
    no-response transport failure.
    """

    status: int | None
    payload: dict[str, Any] | None
    error: str
    elapsed_seconds: float
    provenance: str

    def __post_init__(self) -> None:
        if self.provenance not in PROBE_PROVENANCES:
            raise ValueError(f"unknown probe provenance: {self.provenance!r}")
        if (self.provenance in PROBE_NO_STATUS_PROVENANCES) != (self.status is None):
            raise ValueError("probe status is present exactly when a response was received")
        if (self.provenance == PROBE_JSON_OBJECT) != (self.payload is not None):
            raise ValueError("probe payload is present exactly when the body was a JSON object")

    @property
    def response_received(self) -> bool:
        """True when an HTTP response arrived, whatever its status or body."""

        return self.provenance not in PROBE_NO_STATUS_PROVENANCES

    @property
    def has_verdict(self) -> bool:
        """True when the old revision returned a JSON object we can judge."""

        return self.provenance == PROBE_JSON_OBJECT


@dataclass(frozen=True)
class ProbeRetryPolicy:
    attempts: int = COMPATIBILITY_PROBE_ATTEMPTS
    timeout_seconds: float = 15.0
    backoff_seconds: float = COMPATIBILITY_PROBE_BACKOFF_SECONDS
    max_backoff_seconds: float = COMPATIBILITY_PROBE_MAX_BACKOFF_SECONDS
    deadline_seconds: float = COMPATIBILITY_PROBE_DEADLINE_SECONDS

    def __post_init__(self) -> None:
        # NaN defeats every ordering check below (all comparisons are False) and
        # infinity defeats the finite-deadline contract this policy exists to
        # enforce, so both must be rejected before any bound is interpreted.
        # Without this the socket layer raises deep inside a probe, past the
        # caller that turns a bad policy into a fail-closed report.
        for label, value in (
            ("attempt count", self.attempts),
            ("per-attempt timeout", self.timeout_seconds),
            ("backoff", self.backoff_seconds),
            ("max backoff", self.max_backoff_seconds),
            ("total deadline", self.deadline_seconds),
        ):
            if not math.isfinite(value):
                raise ValueError(f"probe retry policy needs a finite {label}, got {value!r}")
        if self.attempts < 1:
            raise ValueError("probe retry policy needs at least one attempt")
        if self.timeout_seconds <= 0:
            raise ValueError("probe retry policy needs a positive per-attempt timeout")
        if self.backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("probe retry policy backoff must not be negative")
        if self.deadline_seconds <= 0:
            raise ValueError("probe retry policy needs a positive total deadline")

    def backoff_for(self, attempt_index: int) -> float:
        """Exponential backoff before attempt ``attempt_index`` (1-based)."""

        if attempt_index < 2:
            return 0.0
        delay = self.backoff_seconds * (2 ** (attempt_index - 2))
        return min(delay, self.max_backoff_seconds)

    def as_report(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "per_attempt_timeout_seconds": self.timeout_seconds,
            "backoff_seconds": self.backoff_seconds,
            "max_backoff_seconds": self.max_backoff_seconds,
            "total_deadline_seconds": self.deadline_seconds,
        }


@dataclass(frozen=True)
class ProbeResult:
    final: ProbeAttempt
    attempts: list[ProbeAttempt]
    exhausted: str

    @property
    def outcome(self) -> str:
        """`answered` (old revision replied), `rejected` (non-retryable
        defect such as invalid JSON or a URL we cannot request), or the
        exhausted bound that stopped us."""

        if self.exhausted:
            return self.exhausted
        return "answered" if self.final.has_verdict else "rejected"

    def as_report(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "attempt_count": len(self.attempts),
            "elapsed_seconds": round(sum(a.elapsed_seconds for a in self.attempts), 3),
            "attempts": [
                {
                    "status": attempt.status,
                    "error": attempt.error,
                    "elapsed_seconds": round(attempt.elapsed_seconds, 3),
                    "provenance": attempt.provenance,
                    "transient": probe_failure_is_transient(attempt),
                }
                for attempt in self.attempts
            ],
        }

    def retry_detail(self) -> str:
        """Suffix describing how much work the bounded retry contract did."""

        parts = [f"attempts={len(self.attempts)}"]
        parts.append(f"elapsed={sum(a.elapsed_seconds for a in self.attempts):.1f}s")
        if self.exhausted:
            parts.append(self.exhausted)
        return " ".join(parts)


def probe_failure_is_transient(attempt: ProbeAttempt) -> bool:
    """Decide whether ``attempt`` may be retried.

    Only a true no-response outcome is transient: a transport failure or a
    timeout where nothing came back, which is exactly what run 30402570022
    recorded ("The read operation timed out") and exactly what a Cloud Run cold
    start produces.

    Every attempt that *received* an HTTP response is final on attempt 1,
    whatever it contained -- a non-200 version, a body that is not JSON, JSON
    that is not an object, a missing database dependency, or an unhealthy
    database. Retrying a received response would require independent proof
    that the Cloud Run front end rather than the old revision produced it; the
    deployment has no such provenance signal, and treating a status code as
    that proof would let a real verdict (a 503 the old revision itself emitted)
    be retried away.

    A request we could not even build (`invalid_request`) also received no
    response, but it is not transient: nothing was sent, so there is no cold
    start to outlast, and every retry would rebuild the identical broken
    request and burn the deadline before failing the same way.
    """

    return attempt.provenance == PROBE_NO_RESPONSE


def probe_json_endpoint(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    monotonic: Any = time.monotonic,
) -> ProbeAttempt:
    """Perform one probe request, converting every failure into a ProbeAttempt."""

    started = monotonic()
    try:
        status, _content_type, body = _request(url, headers=headers, timeout=timeout)
    except (ValueError, http.client.InvalidURL) as exc:
        # `urllib` refuses to build the request itself -- a malformed URL
        # (`ValueError: Invalid IPv6 URL`, `UnicodeEncodeError` on a non-latin-1
        # host) or a host/header it will not put on the wire
        # (`http.client.InvalidURL`, which is an `HTTPException`, not a
        # `ValueError`). Neither is a transport event and neither is caught by
        # the `OSError` arm below, so before this boundary existed the gate died
        # with a traceback and wrote no compatibility report at all. Classify it
        # as a received-nothing, non-retryable defect: the caller then fails
        # closed with exit 1 and a report the deploy script can read.
        return ProbeAttempt(
            status=None,
            payload=None,
            error=f"{url} is not a requestable URL: {type(exc).__name__}: {exc}",
            elapsed_seconds=monotonic() - started,
            provenance=PROBE_INVALID_REQUEST,
        )
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        return ProbeAttempt(
            status=None,
            payload=None,
            error=str(exc) or type(exc).__name__,
            elapsed_seconds=monotonic() - started,
            provenance=PROBE_NO_RESPONSE,
        )
    elapsed = monotonic() - started
    # Past this point a response exists -- `_request` converts HTTPError into a
    # status and body -- so every remaining outcome is a received response.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return ProbeAttempt(
            status=status,
            payload=None,
            error=f"{url} did not return valid JSON: {exc}",
            elapsed_seconds=elapsed,
            provenance=PROBE_UNPARSEABLE_BODY,
        )
    if not isinstance(payload, dict):
        return ProbeAttempt(
            status=status,
            payload=None,
            error=f"{url} returned a non-object JSON payload",
            elapsed_seconds=elapsed,
            provenance=PROBE_NON_OBJECT_BODY,
        )
    return ProbeAttempt(
        status=status,
        payload=payload,
        error="",
        elapsed_seconds=elapsed,
        provenance=PROBE_JSON_OBJECT,
    )


def probe_with_bounded_retry(
    url: str,
    *,
    headers: Mapping[str, str],
    policy: ProbeRetryPolicy,
    probe: Any = None,
    monotonic: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> ProbeResult:
    """Drive ``probe`` until it yields a verdict, or the bounds are spent.

    The contract is finite in both dimensions: at most ``policy.attempts``
    requests, and never past ``policy.deadline_seconds`` measured from the
    first attempt. A retry is only scheduled when its backoff plus a full
    attempt still fit inside the deadline; the final attempt's timeout is
    clamped to whatever time remains. Both exhaustion modes are failures.
    """

    # Resolved late so tests (and any future caller) can substitute the
    # transport without the default binding freezing at import time.
    resolved_probe = probe if probe is not None else probe_json_endpoint
    started = monotonic()
    attempts: list[ProbeAttempt] = []
    exhausted = ""
    for attempt_index in range(1, policy.attempts + 1):
        backoff = policy.backoff_for(attempt_index)
        if backoff:
            sleep(backoff)
        remaining = policy.deadline_seconds - (monotonic() - started)
        if remaining <= 0:
            exhausted = "deadline_exhausted"
            break
        attempt = resolved_probe(
            url,
            headers=headers,
            timeout=min(policy.timeout_seconds, remaining),
        )
        attempts.append(attempt)
        if not probe_failure_is_transient(attempt):
            return ProbeResult(final=attempt, attempts=attempts, exhausted="")
        if attempt_index == policy.attempts:
            exhausted = "attempts_exhausted"
            break
        next_backoff = policy.backoff_for(attempt_index + 1)
        spent = monotonic() - started
        if spent + next_backoff + policy.timeout_seconds > policy.deadline_seconds:
            exhausted = "deadline_exhausted"
            break

    if not attempts:
        # Only reachable when the deadline was already spent before attempt 1.
        attempts = [
            ProbeAttempt(
                status=None,
                payload=None,
                error=f"probe deadline of {policy.deadline_seconds}s elapsed before any attempt",
                elapsed_seconds=0.0,
                provenance=PROBE_NO_RESPONSE,
            )
        ]
    return ProbeResult(final=attempts[-1], attempts=attempts, exhausted=exhausted)


def compatibility_smoke_checks(
    *,
    api_url: str,
    web_url: str,
    correlation_id: str,
    timeout: float,
    retry_policy: ProbeRetryPolicy | None = None,
    sleep: Any = time.sleep,
) -> tuple[list[CheckResult], dict[str, Any]]:
    """Verify that the old API can still read the migrated production database."""

    policy = retry_policy or ProbeRetryPolicy(timeout_seconds=timeout)
    checks: list[CheckResult] = []
    report: dict[str, Any] = {
        "api_url": api_url.rstrip("/"),
        "web_url": web_url.rstrip("/"),
        "correlation_id": correlation_id,
        "secret_values_redacted": True,
        "probe_retry_policy": policy.as_report(),
    }
    headers = {"x-correlation-id": correlation_id}

    version_result = probe_with_bounded_retry(
        f"{api_url.rstrip('/')}/platform/version",
        headers=headers,
        policy=policy,
        sleep=sleep,
    )
    report["version_probe"] = version_result.as_report()
    version_attempt = version_result.final
    if version_attempt.has_verdict:
        report["version"] = version_attempt.payload
        checks.append(
            CheckResult(
                ok=version_attempt.status == 200,
                name="compatibility:/platform/version:http",
                detail=f"status={version_attempt.status} {version_result.retry_detail()}",
            )
        )
    else:
        checks.append(
            CheckResult(
                False,
                "compatibility:/platform/version:http",
                f"{version_attempt.error} ({version_result.retry_detail()})",
            )
        )

    health_result = probe_with_bounded_retry(
        f"{api_url.rstrip('/')}/platform/health",
        headers=headers,
        policy=policy,
        sleep=sleep,
    )
    report["health_probe"] = health_result.as_report()
    health_attempt = health_result.final
    if health_attempt.has_verdict:
        health = health_attempt.payload or {}
        report["health"] = health
        database = _dependency_text(health, "database")
        database_compatible = health_attempt.status in {200, 503} and _database_reads_healthy(
            health
        )
        checks.append(
            CheckResult(
                ok=database_compatible,
                name="compatibility:/platform/health:database",
                detail=(
                    "old revision remains compatible with the migrated production database"
                    if database_compatible
                    else (
                        f"status={health_attempt.status} "
                        f"database={database or '<missing>'} {health_result.retry_detail()}"
                    )
                ),
            )
        )
    else:
        checks.append(
            CheckResult(
                False,
                "compatibility:/platform/health:database",
                f"{health_attempt.error} ({health_result.retry_detail()})",
            )
        )

    return checks, report


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True).lower()


def _execution_completed(payload: Mapping[str, Any]) -> bool:
    status = payload.get("status")
    if not isinstance(status, Mapping):
        return False
    failed_count = int(status.get("failedCount") or status.get("failed_count") or 0)
    succeeded_count = int(status.get("succeededCount") or status.get("succeeded_count") or 0)
    conditions = status.get("conditions")
    completed = False
    if isinstance(conditions, list):
        for condition in conditions:
            if not isinstance(condition, Mapping):
                continue
            condition_type = str(condition.get("type") or "").lower()
            condition_state = str(
                condition.get("state")
                or condition.get("status")
                or condition.get("conditionState")
                or ""
            ).lower()
            if condition_type in {"completed", "completion"} and condition_state in {
                "true",
                "condition_succeeded",
                "succeeded",
            }:
                completed = True
    return completed and succeeded_count >= 1 and failed_count == 0


_EXECUTION_FRACTION_PATTERN = re.compile(r"\.(\d{1,9})")


def _execution_short_name(entry: Mapping[str, Any], *, index: int) -> str:
    """Return the bare execution name from either the v1 or v2 Job schema."""

    metadata = entry.get("metadata")
    raw = metadata.get("name") if isinstance(metadata, Mapping) else None
    if not isinstance(raw, str) or not raw.strip():
        raw = entry.get("name")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"executions[{index}] has no resolvable execution name")
    return raw.strip().rsplit("/", 1)[-1]


def _execution_created_at(entry: Mapping[str, Any], *, index: int) -> datetime:
    """Return the creation instant from either the v1 or v2 Job schema."""

    metadata = entry.get("metadata")
    raw = metadata.get("creationTimestamp") if isinstance(metadata, Mapping) else None
    if not isinstance(raw, str) or not raw.strip():
        raw = entry.get("createTime") or entry.get("creationTimestamp")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"executions[{index}] has no creation timestamp to order by")
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    # RFC3339 allows nanosecond precision; datetime only carries microseconds.
    text = _EXECUTION_FRACTION_PATTERN.sub(lambda match: f".{match.group(1)[:6]}", text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"executions[{index}] creation timestamp {raw!r} is unparsable") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _execution_matches_job(entry: Mapping[str, Any], *, job: str) -> bool:
    metadata = entry.get("metadata")
    labels = metadata.get("labels") if isinstance(metadata, Mapping) else None
    references: list[str] = []
    if isinstance(labels, Mapping):
        for key in ("run.googleapis.com/job", "job"):
            if key not in labels:
                continue
            value = labels.get(key)
            if not isinstance(value, str) or not value.strip():
                return False
            references.append(value.strip().rsplit("/", 1)[-1])
    if "job" in entry:
        value = entry.get("job")
        if not isinstance(value, str) or not value.strip():
            return False
        references.append(value.strip().rsplit("/", 1)[-1])
    return bool(references) and all(reference == job for reference in references)


def resolve_latest_execution_name(payload: Any, *, job: str | None = None) -> str:
    """Resolve the newest execution name from a `gcloud run jobs executions list` payload.

    `gcloud run jobs executions describe-latest` only exists on recent gcloud
    releases, so job proof capture used to depend on the runner's CLI version.
    The list surface is version-stable but its schema is not: older releases
    emit the Knative shape (`metadata.name` / `metadata.creationTimestamp`) and
    newer ones the v2 shape (`name` / `createTime`). Both are accepted; an
    empty, wrapped-but-empty, malformed, or ambiguous payload fails closed so
    no deployment can fabricate a job receipt.
    """

    entries = payload
    if isinstance(entries, Mapping):
        for key in ("items", "executions"):
            wrapped = entries.get(key)
            if isinstance(wrapped, list):
                entries = wrapped
                break
    if not isinstance(entries, list):
        raise ValueError("executions payload must be a JSON array of execution objects")

    candidates: list[tuple[Mapping[str, Any], str]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"executions[{index}] is not a JSON object")
        name = _execution_short_name(entry, index=index)
        if job and not _execution_matches_job(entry, job=job):
            raise ValueError(f"executions[{index}] {name!r} does not belong to job {job!r}")
        candidates.append((entry, name))

    if not candidates:
        raise ValueError(
            "no Cloud Run Job execution was found; refusing to emit an unproven receipt"
        )
    if len(candidates) == 1:
        return candidates[0][1]

    ordered = sorted(
        (
            (_execution_created_at(entry, index=index), name)
            for index, (entry, name) in enumerate(candidates)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if ordered[0][0] == ordered[1][0]:
        raise ValueError(
            f"executions {ordered[0][1]!r} and {ordered[1][1]!r} share the newest "
            "creation timestamp; the latest execution is ambiguous"
        )
    return ordered[0][1]


class JobDescriptionError(ValueError):
    """A Job description cannot be read as an authoritative task template."""


@dataclass(frozen=True)
class _JobApiSchema:
    """One `gcloud run jobs describe --format=json` dialect.

    A dialect is a single fact, not two independent ones: the API version that
    places task containers at `container_path` is the same API version that
    names secret bindings with `env_source_key`.`secretKeyRef`.`reference_key`.
    Keeping them in one record is what lets the container path a description
    actually uses decide which secret schema that description may use.

    `reference_members` is the closed set of fields this API version defines
    inside `secretKeyRef`: Knative's `SecretKeySelector` carries `name`, `key`,
    `optional`, and the deprecated `localObjectReference`, and Cloud Run v2's
    carries `secret` and `version`. It is the dialect's own fact for the same
    reason the other two are, and it is what lets a planted member inside an
    otherwise valid reference be told apart from the shape gcloud emits.

    A name allowlist is not a shape: it says which members may appear, never
    what they may hold. The remaining fields carry that half of the dialect,
    because the *meaning* of a selector member is as much an API-version fact
    as its name:

    - `version_key` names the member that selects the Secret Manager version
      (Knative `key`, v2 `version`), and `version_required` records that Cloud
      Run v1 documents `key` as required while v2 leaves `version` optional.
    - `mandatory_flag_key` names the member that decides whether the Secret or
      key must exist at all — Knative's `optional`, which v2 does not define.
    - `deprecated_members` are members the version still defines but no longer
      accepts as a source of truth (Knative's `localObjectReference`, the
      pre-`name` way to name the same secret).
    """

    container_path: tuple[str, ...]
    env_source_key: str
    reference_key: str
    reference_members: frozenset[str]
    version_key: str
    version_required: bool
    mandatory_flag_key: str | None
    deprecated_members: frozenset[str]

    @property
    def container_path_label(self) -> str:
        return ".".join(self.container_path)

    @property
    def source_reference_label(self) -> str:
        return f"{self.env_source_key}.secretKeyRef"

    @property
    def reference_label(self) -> str:
        return f"{self.source_reference_label}.{self.reference_key}"


#: The only two dialects `gcloud run jobs describe --format=json` emits:
#: Knative (containers at `spec.template.spec.template.spec.containers`,
#: secrets at `valueFrom.secretKeyRef.name`) and Cloud Run v2 (containers at
#: `template.template.containers`, secrets at
#: `valueSource.secretKeyRef.secret`). Nothing else is a task template, so
#: nothing else may contribute env bindings.
_JOB_API_SCHEMAS: tuple[_JobApiSchema, ...] = (
    _JobApiSchema(
        ("spec", "template", "spec", "template", "spec", "containers"),
        "valueFrom",
        "name",
        frozenset({"name", "key", "optional", "localObjectReference"}),
        version_key="key",
        version_required=True,
        mandatory_flag_key="optional",
        deprecated_members=frozenset({"localObjectReference"}),
    ),
    _JobApiSchema(
        ("template", "template", "containers"),
        "valueSource",
        "secret",
        frozenset({"secret", "version"}),
        version_key="version",
        version_required=False,
        mandatory_flag_key=None,
        deprecated_members=frozenset(),
    ),
)

#: The only env-source member Cloud Run resolves. Knative's `EnvVarSource` also
#: defines `configMapKeyRef`, `fieldRef`, and `resourceFieldRef`, and Cloud Run
#: supports none of them, so their presence beside a valid `secretKeyRef` is
#: never a shape `gcloud run jobs describe` emits.
_ENV_SOURCE_SECRET_MEMBER = "secretKeyRef"


def _resolve_path(payload: Any, path: tuple[str, ...]) -> Any:
    """Return the value at `path`, or `None` when any hop is not a mapping."""

    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _iter_containers_key_paths(payload: Any, prefix: tuple[Any, ...] = ()) -> Iterator[tuple]:
    """Yield the path of every `containers` key anywhere in a description."""

    if isinstance(payload, Mapping):
        for key, value in payload.items():
            path = (*prefix, key)
            if key == "containers":
                yield path
            yield from _iter_containers_key_paths(value, path)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from _iter_containers_key_paths(item, (*prefix, index))


def _authoritative_job_containers(
    job_description: Mapping[str, Any],
) -> tuple[_JobApiSchema, list[Mapping[str, Any]]]:
    """Return the dialect and task containers of a Job description, or fail closed.

    Locating containers by shape let a crafted description satisfy the secret
    proof from anywhere in the payload — `metadata.containers` with planted
    secret refs passed while the real task template bound nothing. Containers
    are therefore read only from the two canonical paths, and a description is
    rejected when they are absent, declared at both paths (ambiguous), or
    accompanied by a `containers` key off those paths.

    The matched path is returned with its schema rather than discarded: it is
    the discriminator that decides which secret-reference dialect the rest of
    the proof will accept.
    """

    present = [
        (schema, _resolve_path(job_description, schema.container_path))
        for schema in _JOB_API_SCHEMAS
        if _resolve_path(job_description, schema.container_path) is not None
    ]
    if not present:
        raise JobDescriptionError(
            "job description declares no containers at "
            f"{' or '.join(schema.container_path_label for schema in _JOB_API_SCHEMAS)}"
        )
    if len(present) > 1:
        raise JobDescriptionError(
            "job description declares containers at both "
            f"{' and '.join(schema.container_path_label for schema, _ in present)}; "
            "the authoritative task template is ambiguous"
        )

    schema, containers = present[0]
    off_path = sorted(
        ".".join(str(key) for key in path)
        for path in _iter_containers_key_paths(job_description)
        if path != schema.container_path
    )
    if off_path:
        raise JobDescriptionError(
            f"job description declares containers outside {schema.container_path_label}: "
            f"{','.join(off_path)}"
        )

    if not isinstance(containers, list) or not containers:
        raise JobDescriptionError(
            f"{schema.container_path_label} is not a non-empty list of containers"
        )
    for index, container in enumerate(containers):
        if not isinstance(container, Mapping):
            raise JobDescriptionError(
                f"{schema.container_path_label}[{index}] is not a container object"
            )
    return schema, list(containers)


def _authoritative_task_container(
    job_description: Mapping[str, Any],
) -> tuple[_JobApiSchema, Mapping[str, Any]]:
    """Return the one container the secret proof may read, or fail closed.

    Reading env across every container in the task template is the same bypass
    one level down: a job whose real task container binds nothing still proved
    the full secret set as long as a sidecar carried it. The deploy script
    (`scripts/deploy_cloud_run_waji.sh`) creates single-container jobs, so a
    second container makes "which container runs the task" unanswerable from
    the description alone and the job is rejected rather than guessed at.
    """

    schema, containers = _authoritative_job_containers(job_description)
    if len(containers) != 1:
        raise JobDescriptionError(
            f"job task template declares {len(containers)} containers; "
            "the authoritative task container is ambiguous"
        )
    return schema, containers[0]


#: A numeric resource component is bounded as well as shaped, and rounds 10 and
#: 11 pinned only the shape. A Secret Manager version number and a Cloud
#: Resource Manager project number are both int64 values, so
#: `9223372036854775808` — and any longer decimal — matched `[1-9][0-9]*` while
#: naming a version Secret Manager cannot hold and a project number Cloud
#: Resource Manager never issues. Both route through the one guard below, which
#: caps them at the signed int64 maximum in the canonical no-leading-zero form
#: the services emit.
_MAX_INT64 = 2**63 - 1
_MAX_INT64_DIGITS = len(str(_MAX_INT64))
_RESOURCE_NUMBER_PATTERN = re.compile(r"[1-9][0-9]*")


def _usable_resource_number(value: str) -> bool:
    """Return whether `value` is a canonical positive int64 resource number.

    The shape is re-stated rather than assumed from the caller's own match, so
    the predicate is total on any string and no caller can route a non-numeric
    component into `int()`. The digit count is checked before the conversion for
    the same reason: an arbitrarily long decimal is out of range by length
    alone, and CPython refuses to convert one past its integer-string limit, so
    counting digits first keeps an oversized selector a rejection rather than an
    exception.
    """

    if _RESOURCE_NUMBER_PATTERN.fullmatch(value) is None:
        return False
    if len(value) > _MAX_INT64_DIGITS:
        return False
    return int(value) <= _MAX_INT64


#: A Secret Manager secret is named in a Cloud Run binding in exactly one of the
#: two forms the API documents, and both are checked here rather than assumed:
#:
#: - the bare secret ID, for a secret in the deploying project. Secret Manager
#:   allows letters, digits, `-` and `_`, up to 255 characters, and nothing else
#:   — no whitespace, no `.`, no `/`, no non-ASCII;
#: - `projects/<project>/secrets/<secret ID>`, for a cross-project secret. The
#:   project segment is a project number (which Secret Manager never writes with
#:   a leading zero) or a project ID: 6 to 30 characters, opening with a
#:   lowercase letter and never closing with a hyphen.
#:
#: Both forms stay accepted because `scripts/deploy_cloud_run_waji.sh` takes each
#: name from an operator-supplied `*_SECRET` variable, so a cross-project secret
#: is a supported deployment and rejecting the path form would over-tighten a
#: schema this task must keep supporting.
#:
#: The project *number* branch is range checked as well as matched, through
#: `_usable_resource_number`; a project *ID* has no range to check.
_SECRET_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,255}")
_SECRET_PROJECT_ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_SECRET_PROJECT_PATTERN = re.compile(
    rf"{_RESOURCE_NUMBER_PATTERN.pattern}|{_SECRET_PROJECT_ID_PATTERN.pattern}"
)
_SECRET_PATH_PATTERN = re.compile(
    rf"projects/(?P<project>{_SECRET_PROJECT_PATTERN.pattern})"
    rf"/secrets/(?:{_SECRET_ID_PATTERN.pattern})"
)


def _usable_secret_name(value: Any) -> bool:
    """Return whether a selector member names a resolvable Secret Manager secret.

    Round 10 closed this same fail-open on the *version* member and named the
    rule that made it a defect: the description is the proof, so the validator
    may not normalize what it is checking. The name member was still read
    through `.strip()` and no grammar at all, which left the rule true of one
    member and false of the one beside it — ` oday-database-url `,
    `oday database url`, a 256-character name, and `.` each named a secret
    Secret Manager does not resolve while the binding passed with zero failing
    checks. A name that is not identical to its own `strip()` is rejected
    outright, for the same reason a version is.

    Round 13 closed the range on the path form's project segment: the segment
    was matched lexically, so a cross-project name carrying a project number
    above the int64 maximum named a project Cloud Resource Manager cannot have
    issued while the binding passed with zero failing checks.
    """

    if not isinstance(value, str):
        return False
    if not value or value != value.strip():
        return False
    if not _configured(value):
        return False
    if _SECRET_ID_PATTERN.fullmatch(value) is not None:
        return True
    path = _SECRET_PATH_PATTERN.fullmatch(value)
    if path is None:
        return False
    project = path.group("project")
    if _SECRET_PROJECT_ID_PATTERN.fullmatch(project) is not None:
        return True
    return _usable_resource_number(project)


def _secret_reference_name(entry: Mapping[str, Any], schema: _JobApiSchema) -> str:
    """Return the Secret Manager reference an env entry binds to, or ``""``.

    Only `schema`'s own pair is accepted — the dialect is fixed by the
    container path the description was resolved at, so a Knative job may bind
    secrets only through `valueFrom.secretKeyRef.name` and a Cloud Run v2 job
    only through `valueSource.secretKeyRef.secret`. Accepting either pair
    regardless of path let a whole description cross over: a Knative-path job
    binding every secret in the v2 dialect, or the reverse, passed the proof
    while describing a shape gcloud never emits. Anything else — the other
    dialect's env source, a top-level `secretKeyRef`, or a key crossed over
    within this dialect such as `valueFrom.secretKeyRef.secret` — proves
    nothing about Secret Manager and resolves to `""`. A reference that is
    absent, a placeholder, or not a name Secret Manager resolves — which
    `_usable_secret_name` decides, rather than the bare non-emptiness this used
    to test — resolves to `""` as well, so the caller fails closed in every
    case.
    """

    source = entry.get(schema.env_source_key)
    if not isinstance(source, Mapping):
        return ""
    reference = source.get("secretKeyRef")
    if not isinstance(reference, Mapping):
        return ""
    value = reference.get(schema.reference_key)
    if isinstance(value, str) and _usable_secret_name(value):
        return value
    return ""


def _foreign_secret_binding_locations(
    entry: Mapping[str, Any], schema: _JobApiSchema
) -> tuple[str, ...]:
    """Return every off-dialect secret-binding location an env entry declares.

    `_secret_reference_name` only *reads* `schema`'s pair; it ignores whatever
    else the entry carries. Ignoring is not rejecting: an entry holding a valid
    `valueFrom.secretKeyRef.name` beside a conflicting
    `valueSource.secretKeyRef.secret` resolved to the first and passed, so a
    description could name one secret to the proof and another to any reader
    that prefers the other dialect. `gcloud run jobs describe` emits exactly one
    dialect per description and exactly one secret source per env entry, so a
    second one is never redundant detail — it makes the binding ambiguous, and
    the caller fails closed instead of picking a winner.

    Reported locations are the other dialect's env source (`valueSource` on a
    Knative job and the reverse), a `secretKeyRef` hoisted to the top level of
    the entry, and the other dialect's reference key inside this dialect's own
    source (`valueFrom.secretKeyRef.secret`).
    """

    locations: list[str] = []
    if "secretKeyRef" in entry:
        locations.append("secretKeyRef")
    for other in _JOB_API_SCHEMAS:
        if other.env_source_key != schema.env_source_key and other.env_source_key in entry:
            locations.append(other.env_source_key)
    source = entry.get(schema.env_source_key)
    reference = source.get("secretKeyRef") if isinstance(source, Mapping) else None
    if isinstance(reference, Mapping):
        for other in _JOB_API_SCHEMAS:
            if other.reference_key != schema.reference_key and other.reference_key in reference:
                locations.append(f"{schema.env_source_key}.secretKeyRef.{other.reference_key}")
    return tuple(sorted(set(locations)))


def _unsupported_secret_source_members(
    entry: Mapping[str, Any], schema: _JobApiSchema
) -> tuple[str, ...]:
    """Return every member inside the accepted secret source Cloud Run cannot resolve.

    Rounds 6 and 7 made the *entry* carry exactly one source; nothing looked
    inside the accepted source itself. `_secret_reference_name` reads
    `secretKeyRef` and `_foreign_secret_binding_locations` reports only the
    other dialect's keys, so an entry holding a valid
    `valueFrom.secretKeyRef.name` beside a `valueFrom.configMapKeyRef` passed
    with zero failing checks — a second env source inside the accepted dialect,
    naming a ConfigMap value Cloud Run v1 explicitly does not support and any
    other reader may prefer. The v2 mirror
    (`valueSource.secretKeyRef` plus `valueSource.configMapKeyRef`) passed the
    same way.

    Two levels are reported, both by presence rather than by payload, matching
    the round-6 and round-7 decision that the key is the defect:

    - members of the env source other than `secretKeyRef` (`valueFrom.fieldRef`,
      `valueSource.configMapKeyRef`, …), none of which Cloud Run resolves;
    - members inside `secretKeyRef` that this dialect's `SecretKeySelector` does
      not define, so a planted field cannot ride along inside an otherwise valid
      reference.

    Cross-dialect reference keys (`valueFrom.secretKeyRef.secret`) are the
    round-6 rule's business and are reported by
    `_foreign_secret_binding_locations`, which the caller consults first.
    """

    source = entry.get(schema.env_source_key)
    if not isinstance(source, Mapping):
        return ()
    locations = [
        f"{schema.env_source_key}.{key}" for key in source if str(key) != _ENV_SOURCE_SECRET_MEMBER
    ]
    reference = source.get(_ENV_SOURCE_SECRET_MEMBER)
    if isinstance(reference, Mapping):
        locations.extend(
            f"{schema.source_reference_label}.{key}"
            for key in reference
            if str(key) not in schema.reference_members
        )
    return tuple(sorted({str(location) for location in locations}))


#: A Secret Manager version selector is exactly one of three things, and the
#: three are not interchangeable spellings of one pattern:
#:
#: - the literal `latest`, lowercase, which is a reserved word rather than an
#:   alias — `Latest` and `LATEST` are neither the literal nor a legal alias;
#: - a positive version number, written canonically and inside the int64 range a
#:   version number is. Secret Manager numbers versions from 1, so `0` is not a
#:   version, it never emits `007`, and `9223372036854775808` is past the last
#:   number a version can carry rather than a very high version;
#: - a version alias: a leading letter, then letters, digits, `_` and `-`, at
#:   most **63** characters. That is the alias limit; 255 is the limit on a
#:   secret *name*, a different resource, and using it here let a 64- to
#:   255-character selector through. `latest` and `NEW` are reserved and cannot
#:   name an alias in any case, so `new`, `New`, and `NEW` resolve to nothing.
_SECRET_VERSION_LITERAL_LATEST = "latest"
_SECRET_VERSION_NUMBER_PATTERN = _RESOURCE_NUMBER_PATTERN
_SECRET_VERSION_ALIAS_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}")
_RESERVED_SECRET_VERSION_ALIASES = frozenset({"latest", "new"})


def _usable_secret_version(value: Any) -> bool:
    """Return whether a selector member names a resolvable secret version.

    The description is the proof, so the selector is read exactly as gcloud
    emitted it. Round 9 stripped first and then validated, which made ` latest `
    prove a binding that Secret Manager does not resolve: whitespace around a
    selector is a defect in the description, not something this validator may
    normalize away on the deployment's behalf. `_usable_secret_name` reads the
    other selector member under the same rule.

    Round 13 added the bound the number carries: a version number is an int64,
    so a selector of `9223372036854775808` or longer pinned a version Secret
    Manager cannot resolve while the mandatory binding around it passed.
    """

    if not isinstance(value, str):
        return False
    if not value or value != value.strip():
        return False
    if not _configured(value):
        return False
    if value == _SECRET_VERSION_LITERAL_LATEST:
        return True
    if _SECRET_VERSION_NUMBER_PATTERN.fullmatch(value):
        return _usable_resource_number(value)
    if value.lower() in _RESERVED_SECRET_VERSION_ALIASES:
        return False
    return _SECRET_VERSION_ALIAS_PATTERN.fullmatch(value) is not None


def _malformed_secret_selector_members(
    entry: Mapping[str, Any], schema: _JobApiSchema
) -> tuple[str, ...]:
    """Return every defined selector member this binding fills in unusably.

    Round 8 closed the reference to the members each dialect defines, but an
    allowlist of *names* says nothing about what those names may hold, so a
    member could be present, defined, and still cancel the binding it sits in.
    Three shapes passed the proof with zero failing checks:

    - `optional: true`, which tells Cloud Run the Secret or key need not exist.
      The env var is then simply absent at runtime, so the binding is not the
      mandatory one the database and every selected provider secret require.
      Only the explicit `false` — not `"false"`, `0`, or `null`, which are not
      the boolean the API defines — means the same thing as leaving it out.
    - a missing `key`, which Cloud Run v1 documents as required. Without it no
      version is selected, so nothing about the reference resolves.
    - a blank or unusable `key`/`version`, which names no version either.

    `localObjectReference` is rejected on presence: it is Knative's superseded
    way of naming the same secret `name` names, so a selector carrying both has
    two names for one binding and gcloud emits neither shape.

    Only member paths are reported, never member payloads, so a planted value
    cannot reach the report through the failure detail.
    """

    source = entry.get(schema.env_source_key)
    if not isinstance(source, Mapping):
        return ()
    reference = source.get(_ENV_SOURCE_SECRET_MEMBER)
    if not isinstance(reference, Mapping):
        return ()

    label = schema.source_reference_label
    problems: list[str] = []
    for member in sorted(schema.deprecated_members):
        if member in reference:
            problems.append(f"{label}.{member} is deprecated and renames the same secret")
    if schema.version_key in reference:
        if not _usable_secret_version(reference[schema.version_key]):
            problems.append(f"{label}.{schema.version_key} is not a usable secret version")
    elif schema.version_required:
        problems.append(f"{label}.{schema.version_key} is required and missing")
    flag_key = schema.mandatory_flag_key
    if flag_key is not None and flag_key in reference and reference[flag_key] is not False:
        problems.append(f"{label}.{flag_key} must be absent or exactly false")
    return tuple(problems)


def _declared_secret_source_locations(
    entry: Mapping[str, Any], schema: _JobApiSchema
) -> tuple[str, ...]:
    """Return every secret-source location an env entry declares, by key presence.

    `_secret_reference_name` reports this dialect's source only when it fully
    *resolves* and `_foreign_secret_binding_locations` reports only the
    off-dialect ones, so an entry carrying `"valueFrom": {}` (or a
    `secretKeyRef` with no usable reference) beside a plaintext value looked
    like a pure literal here while declaring a secret source to any other
    reader. That is how a plaintext `ODP_PRODUCTION_PROVIDER_IDS` with an empty
    same-dialect source was read as the authoritative selection.

    Presence of the source key is the fact this predicate reports; whether the
    reference inside it is usable is a separate question answered by
    `_secret_binding_proof`. `gcloud run jobs describe` emits `value` or a
    secret source for an env entry, never both and never an empty one, so any
    declared source makes a literal beside it unreadable rather than redundant.
    """

    locations = list(_foreign_secret_binding_locations(entry, schema))
    if schema.env_source_key in entry:
        locations.append(schema.env_source_key)
    return tuple(sorted(set(locations)))


def _job_env_entries(
    job_description: Mapping[str, Any],
) -> tuple[_JobApiSchema, dict[str, list[Mapping[str, Any]]]]:
    """Group the authoritative task container's env entries by env-var name.

    Entries are keyed by the name the description declares, never by a
    normalized form of it. Rounds 10 and 11 closed this same fail-open on the
    two `secretKeyRef` members and named the rule behind it — the description is
    the proof, so the validator may not normalize what it checks — but the key
    those members hang off was still read through `name.strip()`, which left the
    rule true of the selector and false of the env var naming it. An entry
    called `"  ODAY_DATABASE_URL  "`, `"\tODP_POI_PROVIDER_API_KEY\n"`, or
    `"ODP_PRODUCTION_PROVIDER_IDS\xa0"` was filed under the required name, so a
    mandatory database or selected-provider secret, and the provider selection
    itself, were proven by an env var whose declared name is not the one the
    runtime reads — `jobs-smoke:<kind>:secret_bindings` reported zero failed
    checks for a container that binds the required name nowhere.

    Matching on the declared name makes each of those fail closed through the
    existing "no env binding is declared" and "job declares no plaintext
    `ODP_PRODUCTION_PROVIDER_IDS`" details, and it tightens nothing a real
    description relies on: every name `scripts/deploy_cloud_run_waji.sh` sets is
    an exact identifier. A blank or non-string name is skipped rather than
    rejected — it can never equal a required name, so it can never prove one.

    Keying by the declared name would on its own have *relaxed* one shape the
    normalizing key rejected: an exact name beside a padded twin collapsed into
    one key and failed as an ambiguous double binding, and under exact keys the
    two are separate env vars, so the exact one would prove the binding alone.
    Whitespace-separated twins are therefore rejected here instead. Which of
    them a reader resolves depends on whether it normalizes — the disagreement
    this round exists to remove — and `gcloud run jobs describe` emits neither
    the twin nor a name that is not identical to its own `strip()`, so the
    description is not a receipt to prove anything from.
    """

    entries: dict[str, list[Mapping[str, Any]]] = {}
    schema, container = _authoritative_task_container(job_description)
    env = container.get("env")
    if not isinstance(env, list):
        return schema, entries
    for entry in env:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        entries.setdefault(name, []).append(entry)

    by_normalized: dict[str, set[str]] = {}
    for name in entries:
        by_normalized.setdefault(name.strip(), set()).add(name)
    twins = sorted(
        normalized for normalized, declared in by_normalized.items() if len(declared) > 1
    )
    if twins:
        raise JobDescriptionError(
            "job task container declares env var names differing only by surrounding "
            f"whitespace ({','.join(twins)}); the authoritative env binding is ambiguous"
        )
    return schema, entries


def _job_selected_provider_ids(
    entries: Mapping[str, list[Mapping[str, Any]]], schema: _JobApiSchema
) -> str:
    """Return the single readable provider selection a Job declares.

    Taking the first nonempty plaintext occurrence let a description declare the
    selection twice — the three normal providers first, `listing.partner_feed`
    second — and be validated against the narrower first value. Exactly one
    occurrence is therefore required inside the authoritative task container,
    and it must be readable plaintext: a duplicate, a secret-bound value, or a
    value that is missing or blank leaves the selection unprovable.
    """

    occurrences = entries.get(PRODUCTION_PROVIDER_IDS_ENV, [])
    if not occurrences:
        raise JobDescriptionError(
            f"job declares no plaintext {PRODUCTION_PROVIDER_IDS_ENV}; "
            "the selected provider set is unprovable"
        )
    if len(occurrences) > 1:
        raise JobDescriptionError(
            f"job declares {PRODUCTION_PROVIDER_IDS_ENV} {len(occurrences)} times; "
            "the selected provider set is ambiguous"
        )

    entry = occurrences[0]
    declared_sources = _declared_secret_source_locations(entry, schema)
    if declared_sources:
        raise JobDescriptionError(
            f"{PRODUCTION_PROVIDER_IDS_ENV} declares secret sources "
            f"({','.join(declared_sources)}) beside its literal value and cannot be read "
            "as plaintext; the selected provider set is unprovable"
        )
    value = entry.get("value")
    if not isinstance(value, str) or not value.strip():
        raise JobDescriptionError(
            f"job declares no plaintext {PRODUCTION_PROVIDER_IDS_ENV}; "
            "the selected provider set is unprovable"
        )
    return value.strip()


def _secret_binding_proof(
    entries: list[Mapping[str, Any]], schema: _JobApiSchema
) -> tuple[bool, str]:
    """Prove one env var has exactly one secret-reference binding, never a literal.

    The reference must be written in `schema`'s dialect — the one belonging to
    the container path this description was resolved at — so a binding borrowed
    from the other API version is no proof at all.

    Exactly one entry may declare the env var, and that entry may declare
    exactly one secret source. Validating every occurrence instead let a
    description bind `ODP_POI_PROVIDER_API_KEY` twice, to two different secrets,
    and pass: both entries are individually well formed, but nothing in the
    description says which one the runtime reads, so neither is proof. The rule
    is uniqueness rather than agreement — matching the
    `ODP_PRODUCTION_PROVIDER_IDS` rule — because a repeated env var has no
    defined winner and `gcloud run jobs deploy --set-secrets` emits it once.

    The literal is rejected on the presence of the `value` key, not on the
    truthiness of what it holds. Testing `isinstance(value, str) and
    value.strip()` alone let a valid `secretKeyRef` pass while the same entry
    declared `"value": ""` (or whitespace, `0`, `false`, `[]`, `{}`, `null`) —
    a second source of truth for the same env var that any reader preferring
    the literal resolves differently. An env entry `gcloud` emits carries a
    literal or a secret source, never both, so the key's presence beside a
    secret source is the defect.

    "Exactly one secret source" is enforced inside the accepted source as well
    as around it: `_unsupported_secret_source_members` rejects any env-source
    member beside `secretKeyRef` — `configMapKeyRef` above all, which Cloud Run
    v1 does not support — and any member inside `secretKeyRef` this dialect's
    selector does not define. Without it a valid `valueFrom.secretKeyRef.name`
    beside a `valueFrom.configMapKeyRef` proved a binding while declaring a
    second source for the same env var.

    Finally the members the dialect does define must be filled in usably, which
    `_malformed_secret_selector_members` decides. A name allowlist accepted
    `optional: true` — a binding Cloud Run is free to resolve to nothing — and a
    missing or blank `key`, which selects no version, as proof of a mandatory
    secret. Both contradict what this proof exists to assert, so both fail
    closed here.
    """

    if not entries:
        return False, "no env binding is declared"
    if len(entries) > 1:
        return False, (
            f"declares {len(entries)} env bindings; the authoritative secret binding is ambiguous"
        )

    entry = entries[0]
    value = entry.get("value")
    if isinstance(value, str) and value.strip():
        return False, "bound to a plaintext value instead of a secret reference"
    if "value" in entry:
        return False, (
            "declares a literal value key beside its secret source; "
            "gcloud emits one or the other, never both"
        )
    foreign = _foreign_secret_binding_locations(entry, schema)
    if foreign:
        return False, (
            f"binding declares off-schema secret sources ({','.join(foreign)}); "
            f"this job's dialect is {schema.reference_label}"
        )
    unsupported = _unsupported_secret_source_members(entry, schema)
    if unsupported:
        return False, (
            f"binding declares env source members Cloud Run does not resolve "
            f"({','.join(unsupported)}); the only supported source is "
            f"{schema.reference_label}"
        )
    if not _secret_reference_name(entry, schema):
        return False, f"binding declares no usable {schema.reference_label}"
    malformed = _malformed_secret_selector_members(entry, schema)
    if malformed:
        return False, (
            f"binding declares an unusable {schema.source_reference_label} "
            f"({'; '.join(malformed)}); a mandatory secret must select a usable "
            "version and may not be optional"
        )
    return True, "bound to a Secret Manager reference (value redacted)"


def required_job_secret_env_vars(
    selected_provider_ids: frozenset[str],
    *,
    root: Path = ROOT,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return required secret env vars for the selected production providers.

    The result is `(required env vars, unknown provider ids, every provider
    credential env var known to the registry)`. Only providers named by
    `ODP_PRODUCTION_PROVIDER_IDS` contribute credentials, which is why an
    unselected provider such as `listing.partner_feed` cannot demand
    `ODP_LISTING_PROVIDER_API_KEY`.
    """

    providers = _provider_definitions(root)
    by_id = {provider.provider_id: provider for provider in providers}
    known_credential_env_vars = tuple(
        sorted(
            {
                credential.env_var
                for provider in providers
                for credential in provider.credentials
                if credential.required_in_live
            }
        )
    )
    unknown_ids = tuple(sorted(selected_provider_ids - by_id.keys()))
    required: set[str] = {DATABASE_SECRET_ENV}
    for provider_id in sorted(selected_provider_ids & by_id.keys()):
        for credential in by_id[provider_id].credentials:
            if credential.required_in_live:
                required.add(credential.env_var)
    return tuple(sorted(required)), unknown_ids, known_credential_env_vars


def job_secret_binding_checks(
    *,
    kind: str,
    job_description: Mapping[str, Any],
    release_provider_ids: str | None = None,
    root: Path = ROOT,
) -> tuple[list[CheckResult], dict[str, Any]]:
    """Prove a Job binds the database and exactly the selected provider secrets.

    Run 30376737123 failed `jobs-smoke:migration:secret_bindings` because the
    validator demanded `ODP_LISTING_PROVIDER_API_KEY` from a substring scan of
    the whole job description while `ODP_PRODUCTION_PROVIDER_IDS` selected only
    `poi.commercial_api`, `geocode.primary_api`, and
    `admin_boundary.official_dataset`. The requirement is now derived from the
    provider registry for the providers the Job itself declares as selected.
    """

    selection_check = f"jobs-smoke:{kind}:provider_selection"
    bindings_check = f"jobs-smoke:{kind}:secret_bindings"
    report: dict[str, Any] = {
        "selected_provider_ids": [],
        "secret_values_redacted": True,
    }

    try:
        schema, entries = _job_env_entries(job_description)
        raw_selection = _job_selected_provider_ids(entries, schema)
    except JobDescriptionError as exc:
        return [
            CheckResult(False, selection_check, str(exc)),
            CheckResult(False, bindings_check, str(exc)),
        ], report

    selected_ids = frozenset(
        provider_id.strip() for provider_id in raw_selection.split(",") if provider_id.strip()
    )
    report["selected_provider_ids"] = sorted(selected_ids)

    if not selected_ids:
        detail = (
            f"job declares no plaintext {PRODUCTION_PROVIDER_IDS_ENV}; "
            "the selected provider set is unprovable"
        )
        return [
            CheckResult(False, selection_check, detail),
            CheckResult(False, bindings_check, detail),
        ], report

    try:
        required_env_vars, unknown_ids, known_credential_env_vars = required_job_secret_env_vars(
            selected_ids, root=root
        )
    except Exception as exc:  # noqa: BLE001 - registry import failure must fail closed
        detail = f"cannot import provider registry: {type(exc).__name__}: {exc}"
        return [
            CheckResult(False, selection_check, detail),
            CheckResult(False, bindings_check, detail),
        ], report

    checks = [
        CheckResult(
            not unknown_ids,
            selection_check,
            (
                f"selected={','.join(sorted(selected_ids))}"
                if not unknown_ids
                else f"unknown provider IDs: {','.join(unknown_ids)}"
            ),
        )
    ]

    if release_provider_ids is not None:
        release_ids = frozenset(
            provider_id.strip()
            for provider_id in release_provider_ids.split(",")
            if provider_id.strip()
        )
        matches = bool(release_ids) and release_ids == selected_ids
        checks.append(
            CheckResult(
                matches,
                f"jobs-smoke:{kind}:selected_provider_release_match",
                (
                    "job selection matches the release provider allowlist"
                    if matches
                    else (
                        f"job selected={','.join(sorted(selected_ids)) or '<none>'} "
                        f"release selected={','.join(sorted(release_ids)) or '<none>'}"
                    )
                ),
            )
        )
        report["release_provider_ids"] = sorted(release_ids)

    failures: list[str] = []
    bound: list[str] = []
    for name in required_env_vars:
        ok, detail = _secret_binding_proof(entries.get(name, []), schema)
        if ok:
            bound.append(name)
        else:
            failures.append(f"{name}: {detail}")
    if unknown_ids:
        failures.append(f"unknown selected provider IDs cannot be proven: {','.join(unknown_ids)}")

    checks.append(
        CheckResult(
            not failures,
            bindings_check,
            (
                "database and every selected provider secret are bound to Secret "
                f"Manager references: {','.join(required_env_vars)}"
                if not failures
                else "; ".join(failures)
            ),
        )
    )

    unselected_bound = sorted(
        name
        for name in known_credential_env_vars
        if name not in required_env_vars and name in entries
    )
    report.update(
        {
            "required_secret_env_vars": list(required_env_vars),
            "secret_bound_env_vars": sorted(bound),
            "unselected_provider_secret_env_vars": unselected_bound,
        }
    )
    return checks, report


def cloud_run_job_checks(
    *,
    kind: str,
    job_description: Mapping[str, Any],
    execution: Mapping[str, Any],
    expected_sha: str,
    release_provider_ids: str | None = None,
    root: Path = ROOT,
) -> tuple[list[CheckResult], dict[str, Any]]:
    """Verify a deployed Job spec and its latest completed execution."""

    description_text = _json_text(job_description)
    execution_text = _json_text(execution)
    expected_mode = "migrate" if kind == "migration" else kind
    secret_checks, secret_report = job_secret_binding_checks(
        kind=kind,
        job_description=job_description,
        release_provider_ids=release_provider_ids,
        root=root,
    )
    checks = [
        CheckResult(
            expected_sha.lower() in description_text,
            f"jobs-smoke:{kind}:release_sha",
            "exact release SHA is present in image/env/labels",
        ),
        CheckResult(
            "scripts/deployment/cloud_run_job_entrypoint.py" in description_text
            and expected_mode in description_text,
            f"jobs-smoke:{kind}:entrypoint",
            f"bounded {expected_mode} entrypoint is configured",
        ),
        *secret_checks,
        CheckResult(
            _execution_completed(execution),
            f"jobs-smoke:{kind}:execution",
            "latest execution completed with succeededCount>=1 and failedCount=0",
        ),
        CheckResult(
            expected_sha.lower() in execution_text or bool(execution.get("status")),
            f"jobs-smoke:{kind}:execution_receipt",
            "execution has a queryable Cloud Run status receipt",
        ),
    ]
    report = {
        "job_kind": kind,
        "expected_sha": expected_sha,
        "job_name": (
            job_description.get("metadata", {}).get("name")
            if isinstance(job_description.get("metadata"), Mapping)
            else job_description.get("name")
        ),
        "execution_name": (
            execution.get("metadata", {}).get("name")
            if isinstance(execution.get("metadata"), Mapping)
            else execution.get("name")
        ),
    }
    report.update(secret_report)
    return checks, report


def _finalize(
    *,
    checks: list[CheckResult],
    report: dict[str, Any],
    output: Path | None,
    label: str,
) -> int:
    report.update(
        {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "checks": [asdict(check) for check in checks],
            "ok": all(check.ok for check in checks),
        }
    )
    _write_report(output, report)
    if report["ok"]:
        print(f"{label} passed.")
        if output:
            print(f"report={output}")
        return 0

    print(f"{label} failed (fail-closed):")
    for check in checks:
        if not check.ok:
            print(f"- {check.name}: {check.detail}")
    if output:
        print(f"report={output}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--environment", required=True)
    preflight.add_argument("--release-sha", required=True)
    preflight.add_argument("--root", type=Path, default=ROOT)
    preflight.add_argument("--output", type=Path)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--api-url", required=True)
    smoke.add_argument("--web-url", required=True)
    smoke.add_argument("--expected-sha", required=True)
    smoke.add_argument("--correlation-id", default=f"corr-cloud-run-{int(time.time())}")
    smoke.add_argument("--timeout", type=float, default=15.0)
    smoke.add_argument("--output", type=Path)

    compatibility_smoke = subparsers.add_parser("compatibility-smoke")
    compatibility_smoke.add_argument("--api-url", required=True)
    compatibility_smoke.add_argument("--web-url", required=True)
    compatibility_smoke.add_argument(
        "--correlation-id", default=f"corr-cloud-run-compat-{int(time.time())}"
    )
    compatibility_smoke.add_argument("--timeout", type=float, default=15.0)
    compatibility_smoke.add_argument(
        "--compat-retry-attempts", type=int, default=COMPATIBILITY_PROBE_ATTEMPTS
    )
    compatibility_smoke.add_argument(
        "--compat-retry-backoff-seconds",
        type=float,
        default=COMPATIBILITY_PROBE_BACKOFF_SECONDS,
    )
    compatibility_smoke.add_argument(
        "--compat-retry-max-backoff-seconds",
        type=float,
        default=COMPATIBILITY_PROBE_MAX_BACKOFF_SECONDS,
    )
    compatibility_smoke.add_argument(
        "--compat-retry-deadline-seconds",
        type=float,
        default=COMPATIBILITY_PROBE_DEADLINE_SECONDS,
    )
    compatibility_smoke.add_argument("--output", type=Path)

    jobs_smoke = subparsers.add_parser("jobs-smoke")
    jobs_smoke.add_argument(
        "--job-kind", required=True, choices=("migration", "worker", "scheduler")
    )
    jobs_smoke.add_argument("--job-description", required=True, type=Path)
    jobs_smoke.add_argument("--execution", required=True, type=Path)
    jobs_smoke.add_argument("--expected-sha", required=True)
    jobs_smoke.add_argument("--output", type=Path)

    resolve_execution = subparsers.add_parser("resolve-latest-execution")
    resolve_execution.add_argument("--executions", required=True, type=Path)
    resolve_execution.add_argument("--job")

    args = parser.parse_args()
    if args.command == "resolve-latest-execution":
        try:
            payload = json.loads(args.executions.read_text(encoding="utf-8"))
            name = resolve_latest_execution_name(payload, job=args.job)
        except (OSError, ValueError) as exc:
            print(
                "Cloud Run latest execution resolution failed (fail-closed): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        print(name)
        return 0

    if args.command == "preflight":
        checks = preflight_checks(
            env=os.environ,
            expected_environment=args.environment,
            expected_sha=args.release_sha,
            root=args.root,
        )
        report = {
            "environment": args.environment,
            "release_sha": args.release_sha,
            "secret_values_redacted": True,
        }
        return _finalize(
            checks=checks,
            report=report,
            output=args.output,
            label="Cloud Run live deployment preflight",
        )

    if args.command == "jobs-smoke":
        try:
            job_description = _read_json_object(args.job_description)
            execution = _read_json_object(args.execution)
            checks, report = cloud_run_job_checks(
                kind=args.job_kind,
                job_description=job_description,
                execution=execution,
                expected_sha=args.expected_sha,
                release_provider_ids=os.environ.get(PRODUCTION_PROVIDER_IDS_ENV),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            checks = [
                CheckResult(
                    False,
                    f"jobs-smoke:{args.job_kind}:artifact",
                    f"{type(exc).__name__}: {exc}",
                )
            ]
            report = {
                "job_kind": args.job_kind,
                "expected_sha": args.expected_sha,
                "secret_values_redacted": True,
            }
        return _finalize(
            checks=checks,
            report=report,
            output=args.output,
            label=f"Cloud Run {args.job_kind} Job smoke",
        )

    if args.command == "compatibility-smoke":
        try:
            policy = ProbeRetryPolicy(
                attempts=args.compat_retry_attempts,
                timeout_seconds=args.timeout,
                backoff_seconds=args.compat_retry_backoff_seconds,
                max_backoff_seconds=args.compat_retry_max_backoff_seconds,
                deadline_seconds=args.compat_retry_deadline_seconds,
            )
        except ValueError as exc:
            return _finalize(
                checks=[CheckResult(False, "compatibility:retry_policy", str(exc))],
                report={"secret_values_redacted": True},
                output=args.output,
                label="Cloud Run migration compatibility smoke",
            )
        checks, report = compatibility_smoke_checks(
            api_url=args.api_url,
            web_url=args.web_url,
            correlation_id=args.correlation_id,
            timeout=args.timeout,
            retry_policy=policy,
        )
    else:
        token = os.environ.get("ODP_OPERATOR_SMOKE_BEARER_TOKEN", "")
        checks = []
        if not token.strip():
            checks.append(
                CheckResult(
                    False,
                    "secret:ODP_OPERATOR_SMOKE_BEARER_TOKEN",
                    "missing; authenticated operator bootstrap cannot be verified",
                )
            )
            report = {"secret_values_redacted": True}
        else:
            checks, report = smoke_checks(
                api_url=args.api_url,
                web_url=args.web_url,
                expected_sha=args.expected_sha,
                bearer_token=token,
                operator_role=os.environ.get("ODP_OPERATOR_SMOKE_ROLE", ""),
                operator_subject=os.environ.get("ODP_OPERATOR_SMOKE_SUBJECT", ""),
                operator_tenant=os.environ.get("ODP_OPERATOR_SMOKE_TENANT", ""),
                correlation_id=args.correlation_id,
                timeout=args.timeout,
            )
    return _finalize(
        checks=checks,
        report=report,
        output=args.output,
        label=(
            "Cloud Run live deployment smoke"
            if args.command == "smoke"
            else "Cloud Run migration compatibility smoke"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
