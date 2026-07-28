#!/usr/bin/env python3
"""Fail-closed preflight and smoke validation for Cloud Run deployments.

The deployment contract deliberately rejects a configured-looking environment
when the repository can only start memory/fixture-backed services. Secret
values are consumed for authenticated smoke requests but are never emitted.
"""

from __future__ import annotations

import argparse
import contextlib
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
        expected_login_prefix = f"{web_url.rstrip('/')}/login?"
        auth_redirect = (
            web_status in {302, 303, 307, 308}
            and isinstance(location, str)
            and location.startswith(expected_login_prefix)
            and "returnTo=" in location
        )
        checks.append(
            CheckResult(
                ok=auth_redirect,
                name="smoke:web:/operator",
                detail=f"status={web_status} protected_redirect={str(auth_redirect).lower()}",
            )
        )
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        checks.append(CheckResult(False, "smoke:web:/operator", str(exc)))

    return checks, report


def compatibility_smoke_checks(
    *,
    api_url: str,
    web_url: str,
    correlation_id: str,
    timeout: float,
) -> tuple[list[CheckResult], dict[str, Any]]:
    """Verify that the old API can still read the migrated production database."""

    checks: list[CheckResult] = []
    report: dict[str, Any] = {
        "api_url": api_url.rstrip("/"),
        "web_url": web_url.rstrip("/"),
        "correlation_id": correlation_id,
        "secret_values_redacted": True,
    }
    headers = {"x-correlation-id": correlation_id}

    try:
        version_status, version = _json_request(
            f"{api_url.rstrip('/')}/platform/version",
            headers=headers,
            timeout=timeout,
        )
        report["version"] = version
        checks.append(
            CheckResult(
                ok=version_status == 200,
                name="compatibility:/platform/version:http",
                detail=f"status={version_status}",
            )
        )
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        checks.append(CheckResult(False, "compatibility:/platform/version:http", str(exc)))

    try:
        health_status, health = _json_request(
            f"{api_url.rstrip('/')}/platform/health",
            headers=headers,
            timeout=timeout,
        )
        report["health"] = health
        database = _dependency_text(health, "database")
        database_compatible = (
            health_status in {200, 503}
            and bool(database)
            and "healthy" in database
            and not _contains_forbidden_marker(database)
        )
        checks.append(
            CheckResult(
                ok=database_compatible,
                name="compatibility:/platform/health:database",
                detail=(
                    "old revision remains compatible with the migrated production database"
                    if database_compatible
                    else f"status={health_status} database={database or '<missing>'}"
                ),
            )
        )
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        checks.append(CheckResult(False, "compatibility:/platform/health:database", str(exc)))

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


#: The only two paths `gcloud run jobs describe --format=json` places task
#: containers at: Knative (`spec.template.spec.template.spec.containers`) and
#: Cloud Run v2 (`template.template.containers`). Nothing else is a task
#: template, so nothing else may contribute env bindings.
_JOB_CONTAINER_PATHS: tuple[tuple[str, ...], ...] = (
    ("spec", "template", "spec", "template", "spec", "containers"),
    ("template", "template", "containers"),
)


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


def _authoritative_job_containers(job_description: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the task containers of a Job description, or fail closed.

    Locating containers by shape let a crafted description satisfy the secret
    proof from anywhere in the payload — `metadata.containers` with planted
    secret refs passed while the real task template bound nothing. Containers
    are therefore read only from the two canonical paths, and a description is
    rejected when they are absent, declared at both paths (ambiguous), or
    accompanied by a `containers` key off those paths.
    """

    present = [
        (path, _resolve_path(job_description, path))
        for path in _JOB_CONTAINER_PATHS
        if _resolve_path(job_description, path) is not None
    ]
    if not present:
        raise JobDescriptionError(
            "job description declares no containers at "
            f"{' or '.join('.'.join(path) for path in _JOB_CONTAINER_PATHS)}"
        )
    if len(present) > 1:
        raise JobDescriptionError(
            "job description declares containers at both "
            f"{' and '.join('.'.join(path) for path, _ in present)}; "
            "the authoritative task template is ambiguous"
        )

    canonical_path, containers = present[0]
    off_path = sorted(
        ".".join(str(key) for key in path)
        for path in _iter_containers_key_paths(job_description)
        if path != canonical_path
    )
    if off_path:
        raise JobDescriptionError(
            f"job description declares containers outside {'.'.join(canonical_path)}: "
            f"{','.join(off_path)}"
        )

    if not isinstance(containers, list) or not containers:
        raise JobDescriptionError(
            f"{'.'.join(canonical_path)} is not a non-empty list of containers"
        )
    for index, container in enumerate(containers):
        if not isinstance(container, Mapping):
            raise JobDescriptionError(
                f"{'.'.join(canonical_path)}[{index}] is not a container object"
            )
    return list(containers)


#: The only two env-var-to-secret schemas Cloud Run emits, as
#: `(env source field, secret reference field)`. Knative names the secret in
#: `valueFrom.secretKeyRef.name`; Cloud Run v2 names it in
#: `valueSource.secretKeyRef.secret`.
_SECRET_REFERENCE_SCHEMAS: tuple[tuple[str, str], ...] = (
    ("valueFrom", "name"),
    ("valueSource", "secret"),
)


def _secret_reference_name(entry: Mapping[str, Any]) -> str:
    """Return the Secret Manager reference an env entry binds to, or ``""``.

    Only the two documented schema/key pairs in `_SECRET_REFERENCE_SCHEMAS` are
    accepted. Anything else — a top-level `secretKeyRef`, or a key crossed over
    from the other schema such as `valueFrom.secretKeyRef.secret` or
    `valueSource.secretKeyRef.name` — is not a binding gcloud emits, so it
    proves nothing about Secret Manager and resolves to `""`. A reference that
    is absent, empty, or a placeholder resolves to `""` as well, so the caller
    fails closed in every case.
    """

    for source_key, reference_key in _SECRET_REFERENCE_SCHEMAS:
        source = entry.get(source_key)
        if not isinstance(source, Mapping):
            continue
        reference = source.get("secretKeyRef")
        if not isinstance(reference, Mapping):
            continue
        value = reference.get(reference_key)
        if isinstance(value, str) and _configured(value):
            return value.strip()
    return ""


def _job_env_entries(job_description: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    """Group every container env entry of a Job description by env-var name."""

    entries: dict[str, list[Mapping[str, Any]]] = {}
    for container in _authoritative_job_containers(job_description):
        env = container.get("env")
        if not isinstance(env, list):
            continue
        for entry in env:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            entries.setdefault(name.strip(), []).append(entry)
    return entries


def _job_selected_provider_ids(entries: Mapping[str, list[Mapping[str, Any]]]) -> str:
    """Return the single readable provider selection a Job declares.

    Taking the first nonempty plaintext occurrence let a description declare the
    selection twice — the three normal providers first, `listing.partner_feed`
    second — and be validated against the narrower first value. Exactly one
    occurrence is therefore required across the authoritative container set, and
    it must be readable plaintext: a duplicate, a secret-bound value, or a value
    that is missing or blank leaves the selection unprovable.
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
    if _secret_reference_name(entry):
        raise JobDescriptionError(
            f"{PRODUCTION_PROVIDER_IDS_ENV} is bound to a secret reference and cannot be read; "
            "the selected provider set is unprovable"
        )
    value = entry.get("value")
    if not isinstance(value, str) or not value.strip():
        raise JobDescriptionError(
            f"job declares no plaintext {PRODUCTION_PROVIDER_IDS_ENV}; "
            "the selected provider set is unprovable"
        )
    return value.strip()


def _secret_binding_proof(entries: list[Mapping[str, Any]]) -> tuple[bool, str]:
    """Prove one env var is bound to a secret reference, never to a literal."""

    if not entries:
        return False, "no env binding is declared"
    for entry in entries:
        value = entry.get("value")
        if isinstance(value, str) and value.strip():
            return False, "bound to a plaintext value instead of a secret reference"
        if not _secret_reference_name(entry):
            return False, "binding declares no usable secretKeyRef"
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
        entries = _job_env_entries(job_description)
        raw_selection = _job_selected_provider_ids(entries)
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
        ok, detail = _secret_binding_proof(entries.get(name, []))
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
        checks, report = compatibility_smoke_checks(
            api_url=args.api_url,
            web_url=args.web_url,
            correlation_id=args.correlation_id,
            timeout=args.timeout,
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
