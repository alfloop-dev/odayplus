#!/usr/bin/env python3
"""Fail-closed preflight and smoke validation for Cloud Run deployments.

The deployment contract deliberately rejects a configured-looking environment
when the repository can only start memory/fixture-backed services. Secret
values are consumed for authenticated smoke requests but are never emitted.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
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
REQUIRED_PRODUCT_PROVIDER_IDS = frozenset(
    {
        "listing.partner_feed",
        "poi.commercial_api",
        "geocode.primary_api",
        "admin_boundary.official_dataset",
    }
)

REQUIRED_PUBLIC_CONFIG = (
    "GCP_PROJECT",
    "GCP_REGION",
    "GCP_AR_REPO",
    "GCP_CLOUD_SQL_INSTANCE",
    "API_SERVICE",
    "WEB_SERVICE",
    "ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT",
    "ODP_SNAPSHOT_BUCKET",
    "ODP_LISTING_PROVIDER_FEED_URL",
    "ODP_GEOCODE_PROVIDER_URL",
    "ODP_LISTING_PROVIDER_AUTH_STATUS",
    "ODP_POI_PROVIDER_AUTH_STATUS",
    "ODP_GEOCODE_PROVIDER_AUTH_STATUS",
    "ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS",
    PRODUCTION_PROVIDER_IDS_ENV,
    "ODP_AUTH_ISSUER",
    "ODP_AUTH_AUDIENCES",
    "ODP_OPERATOR_SMOKE_ROLE",
    "ODP_OPERATOR_SMOKE_SUBJECT",
    "ODP_OPERATOR_SMOKE_TENANT",
)
REQUIRED_SECRET_REFERENCES = (
    "ODAY_DATABASE_URL_SECRET",
    "ODP_LISTING_PROVIDER_API_KEY_SECRET",
    "ODP_POI_PROVIDER_API_KEY_SECRET",
    "ODP_GEOCODE_PROVIDER_API_KEY_SECRET",
    "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN_SECRET",
    "ODP_AUTH_HS256_KEYS_SECRET",
)
REQUIRED_SECRET_VALUES = ("ODP_OPERATOR_SMOKE_BEARER_TOKEN",)
REQUIRED_RUNTIME_VALUES = {
    "ODP_REQUIRE_LIVE_DATA": "true",
    "ODP_DATA_BINDING_MODE": "live",
    "ODP_PRODUCT_MODE": "production",
    "ODP_EXTERNAL_PROVIDER_MODE": "live",
    "ODP_PERSISTENCE": "postgresql",
    "ODP_OBJECT_STORE": "gcs",
    "ODP_COMPETITOR_MANUAL_SOURCE_STATUS": "disabled",
}


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    name: str
    detail: str


def _configured(value: str) -> bool:
    return value.strip().lower() not in PLACEHOLDER_VALUES


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
    deploys_worker = bool(
        re.search(
            r"gcloud\s+run\s+(?:jobs\s+deploy|deploy\s+[\"']?\$\{?WORKER_SERVICE)",
            deploy_text,
        )
    )

    operator_state = root / "modules/opsboard/application/operator_state.py"
    operator_text = operator_state.read_text(encoding="utf-8") if operator_state.exists() else ""
    bootstrap_uses_seed = bool(
        re.search(r"self\._state[^=\n]*=\s*load_r4_seed\(\)", operator_text)
    )
    has_live_operator_repository = bool(
        re.search(
            r"(?:self\._live_repository|operator_repository\s*:)",
            operator_text,
        )
    )
    provider_registry = root / "modules/external_data/connectors/provider_registry.py"
    provider_registry_text = (
        provider_registry.read_text(encoding="utf-8") if provider_registry.exists() else ""
    )
    registry_honors_production_allowlist = (
        PRODUCTION_PROVIDER_IDS_ENV in provider_registry_text
    )

    checks = [
        CheckResult(
            ok=has_postgres_adapter and factory_selects_postgres,
            name="repository:production_database_adapter",
            detail=(
                "supported PostgreSQL persistence adapter is wired through build_persistence"
                if has_postgres_adapter and factory_selects_postgres
                else (
                    "missing: build_persistence supports only memory/SQLite and unknown modes "
                    "fall back to memory"
                )
            ),
        ),
        CheckResult(
            ok=worker_dockerfile.exists() and deploys_worker,
            name="repository:worker_runtime",
            detail=(
                "worker image and Cloud Run worker deployment are present"
                if worker_dockerfile.exists() and deploys_worker
                else "missing: no deployable worker image and Cloud Run worker runtime"
            ),
        ),
        CheckResult(
            ok=not bootstrap_uses_seed and has_live_operator_repository,
            name="repository:operator_bootstrap_data_source",
            detail=(
                "operator bootstrap is wired to a live repository"
                if not bootstrap_uses_seed and has_live_operator_repository
                else (
                    "missing: OperatorStateService initializes /api/v1/operator/bootstrap "
                    "from canonical R4 seed data"
                )
                if bootstrap_uses_seed
                else (
                    "missing: OperatorStateService has no live operator repository; "
                    "an empty/unavailable response is fail-closed but is not real data"
                )
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
    checks.extend(provider_adapter_checks(root, production_provider_ids=production_provider_ids))
    return checks


def _provider_definitions(root: Path) -> tuple[Any, ...]:
    """Import and return the provider registry definitions."""

    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    registry_module = importlib.import_module(
        "modules.external_data.connectors.provider_registry"
    )
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


def _contains_forbidden_marker(value: str) -> bool:
    normalized = value.lower()
    return any(marker in normalized for marker in FORBIDDEN_DATA_MARKERS)


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
    expected_sha: str,
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

    version = payloads.get("version", {})
    actual_sha = str(version.get("release_sha") or "").strip().lower()
    checks.append(
        CheckResult(
            ok=actual_sha == expected_sha.strip().lower(),
            name="smoke:/platform/version:release_sha",
            detail=f"expected={expected_sha.strip().lower()} actual={actual_sha or '<missing>'}",
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
    providers = _dependency_text(health, "external_providers")
    checks.append(
        CheckResult(
            ok=bool(providers)
            and "healthy" in providers
            and not _contains_forbidden_marker(providers),
            name="smoke:/platform/health:external_providers",
            detail=(
                "live providers healthy"
                if providers and "healthy" in providers and not _contains_forbidden_marker(providers)
                else "missing, unhealthy, or fixture provider mode"
            ),
        )
    )
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
        "x-subject-id": operator_subject,
        "x-tenant-id": operator_tenant,
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
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        checks.append(CheckResult(False, "smoke:/api/v1/operator/bootstrap:http", str(exc)))

    try:
        web_status, _, web_body = _request(
            f"{web_url.rstrip('/')}/operator",
            headers=base_headers,
            timeout=timeout,
        )
        stale_static_shell = "operator-design/index.html" in web_body
        checks.append(
            CheckResult(
                ok=web_status == 200 and not stale_static_shell,
                name="smoke:web:/operator",
                detail=f"status={web_status} stale_static_shell={str(stale_static_shell).lower()}",
            )
        )
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        checks.append(CheckResult(False, "smoke:web:/operator", str(exc)))

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

    args = parser.parse_args()
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

    token = os.environ.get("ODP_OPERATOR_SMOKE_BEARER_TOKEN", "")
    checks: list[CheckResult] = []
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
        label="Cloud Run live deployment smoke",
    )


if __name__ == "__main__":
    raise SystemExit(main())
