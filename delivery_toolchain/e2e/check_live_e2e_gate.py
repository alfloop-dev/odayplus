#!/usr/bin/env python3
"""Fail-closed live end-to-end acceptance gate for a deployed ODay Plus release.

This gate runs *after* a deployment has published a release and drives the
deployed runtime the way a real operator would: authenticate, read the operator
bootstrap, enqueue durable work, watch the worker take it to a terminal state,
and read the durable audit receipt back out.

It is deliberately complementary to the two gates that already exist:

* ``product_ops/deployment/validate_cloud_run_live_deployment.py`` proves the
  *deployment topology* (preflight config, Cloud Run job receipts, candidate
  revision smoke);
* ``delivery_toolchain/e2e/check_live_production_data.py`` proves the *data plane* by
  reading PostgreSQL directly and reconciling it against a commit-bound
  evidence manifest.

This gate proves the *product path through the deployed release*: every
assertion is made against the live HTTP surface bound to one exact release SHA.

Everything fails closed. A missing input, an unreachable dependency, an
unauthenticated request that is *not* rejected, a missing MLflow ``production``
alias, an empty dataset, a job that never reaches ``succeeded``, a missing audit
receipt, or any fixture/mock/seed marker anywhere in a response body blocks the
release. Every blocker is reported with the runtime dependency that owns it and
the next action that would clear it, so a red gate names the thing to fix rather
than the assertion that noticed.

Secrets are read from named environment variables and are never written to the
report.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess  # noqa: S404 - fixed argv, no shell, CI-controlled worker trigger
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".odp_data" / "live-e2e-gate" / "live-e2e-gate-report.json"
LIVE_DATA_GATE = ROOT / "delivery_toolchain" / "e2e" / "check_live_production_data.py"

API_URL_ENV = "ODP_LIVE_E2E_API_URL"
WEB_URL_ENV = "ODP_LIVE_E2E_WEB_URL"
EXPECTED_SHA_ENV = "ODAY_RELEASE_SHA"
BEARER_TOKEN_ENV = "ODP_OPERATOR_SMOKE_BEARER_TOKEN"
OPERATOR_ROLE_ENV = "ODP_OPERATOR_SMOKE_ROLE"
PRODUCTION_PROVIDER_IDS_ENV = "ODP_PRODUCTION_PROVIDER_IDS"

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
POSTGRES_MODES = frozenset({"postgres", "postgresql"})
# The two surfaces that publish operator provenance spell a healthy origin
# differently. ``/readiness`` returns the repository's own kind
# (``OperatorLiveRepository.data_origin`` -> ``authoritative``); the operator
# envelope rewrites ``meta.dataOrigin.kind`` to the resolved data mode
# (``live``). Both healthy spellings are accepted here so the gate does not
# fail closed on a value the runtime can never emit. The surrogate spellings
# (``fixture``, ``r4-seed``) and the degraded spelling (``unavailable``) are
# still rejected.
LIVE_ORIGIN_KINDS = frozenset({"authoritative", "live"})
ARTIFACT_SCHEMES = frozenset({"gs", "https", "s3", "mlflow-artifacts"})
DENIED_STATUSES = frozenset({401, 403})
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
ACCEPTED_ENQUEUE_STATUSES = frozenset({"queued", "running", "succeeded"})
FORBIDDEN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "example.com"})

# ``modules.external_data.connectors.provider_registry`` owns the canonical set;
# it is duplicated here (and in validate_cloud_run_live_deployment.py) because a
# release gate must not import runtime code from the artefact it is judging.
DEFAULT_REQUIRED_PROVIDER_IDS = (
    "admin_boundary.official_dataset",
    "geocode.primary_api",
    "poi.commercial_api",
)

# Being *required in live mode* and being *able to produce an ingestion run* are
# two different facts, and conflating them is how this gate can demand evidence
# the runtime is structurally incapable of emitting.
#
# Only providers whose registry category is snapshot-schedulable ever reach
# ``ExternalFetchScheduler.run_once`` -- the single writer of ingestion runs,
# behind both ``handle_external_fetch`` and ``POST /external-data/ingestion-runs``.
# Everything else (geocode is an address-lookup enrichment source, competitor is
# manual attestation) raises ``provider_not_schedulable``, so a ``SUCCEEDED``
# ingestion run for it cannot exist in *any* healthy environment.
#
# Liveness for those providers is therefore proven on the surface that actually
# exercises them: ``/readiness`` -> ``details.provider.probeEvidence``, whose
# geocode probe issues a real authenticated POST and validates the returned
# coordinates. That probe runs for every required provider, so the snapshot
# providers are covered by it *and* by their persisted ingestion run.
#
# Both maps mirror ``provider_registry()`` and
# ``scheduled_fetch._SCHEDULABLE_CATEGORIES``; they are pinned rather than
# imported for the reason above, and tests/e2e/test_live_e2e_gate.py binds them
# back to the runtime so an unmirrored registry change fails the anti-drift suite.
PROVIDER_CATEGORIES: Mapping[str, str] = {
    "listing.partner_feed": "listing",
    "poi.commercial_api": "poi",
    "geocode.primary_api": "geocode",
    "admin_boundary.official_dataset": "admin_boundary",
    "competitor.manual_source": "competitor_manual",
    "store_opening_authority": "store_opening_authority",
}
SNAPSHOT_SCHEDULABLE_CATEGORIES = frozenset({"listing", "poi", "admin_boundary"})

# models.shared_ml.production_contracts.PRODUCTION_MODEL_CONTRACTS, pinned here
# for the same reason. A service whose MLflow ``production`` alias is missing
# must fail the gate rather than silently shrink the required set.
#
# GOVERNED_DISABLED_SERVICES: services that are governed-disabled in the current
# PG16 data maturity cycle. A governed-disabled service must NOT have a
# production alias in MLflow; instead the runtime capability record must carry
# full receipt-backed evidence (reasonCode equal to the capability record's,
# real observed/eligible counts with observedAt + inventoryVersion lineage, a
# separate activationThreshold, source contract, owner, activation gate, and
# autoSeeded=False). The gate accepts absence of a production alias for these
# services ONLY when their capability record has complete evidence.
# ForecastOps is always required to have a real approved production alias.
REQUIRED_MODEL_BINDINGS: Mapping[str, str] = {
    "avm": "dealroom_avm",
    "forecastops": "forecast_revenue_interval",
    "heatzone": "heatzone_priority",
    "sitescore": "sitescore_propensity",
}
# Services that are governed-disabled in the current data-maturity cycle.
# Must stay in sync with production_contracts.governed_disabled_services();
# anti-drift tests in tests/e2e/test_live_e2e_gate.py enforce this.
GOVERNED_DISABLED_SERVICES: frozenset[str] = frozenset({"avm", "heatzone", "sitescore"})
# Required evidence fields that every governed-disabled capability must expose.
# Counts are receipt-backed observations (observedAt + inventoryVersion carry
# the lineage); activationThreshold is the separate policy gate, never a count.
_GOVERNED_DISABLED_EVIDENCE_FIELDS = (
    "reasonCode",
    "observedCount",
    "eligibleCount",
    "activationThreshold",
    "sourceContract",
    "owner",
    "activationGate",
    "observedAt",
    "inventoryVersion",
)
_GOVERNED_DISABLED_TEXT_FIELDS = (
    "reasonCode",
    "sourceContract",
    "owner",
    "activationGate",
    "observedAt",
    "inventoryVersion",
)
PRODUCTION_ALIAS = "production"

WORKER_PROBE_JOB_TYPE = "external-fetch"

DEPENDENCY_ACTIONS: Mapping[str, str] = {
    "config": (
        "Set the gate inputs (API URL, web URL, release SHA, operator bearer "
        "token, role, ODP_PRODUCTION_PROVIDER_IDS) in the deploy workflow "
        "environment."
    ),
    "release": (
        "Re-run the deployment so the serving revision carries the expected "
        "release SHA, or point the gate at the revision that was actually promoted."
    ),
    "api-runtime": (
        "Inspect the deployed API revision logs; the runtime refused to report a "
        "ready live state."
    ),
    "auth": (
        "Repair the OIDC/JWKS boundary (ODP_AUTH_ISSUER, ODP_AUTH_AUDIENCES, "
        "ODP_AUTH_JWKS_URI) and the operator smoke credential."
    ),
    "postgresql": (
        "Restore the production Cloud SQL PostgreSQL binding "
        "(ODAY_DATABASE_URL, Cloud SQL instance attachment, migrations)."
    ),
    "object-store": (
        "Restore the GCS artifact binding (ODP_SNAPSHOT_BUCKET and the MLflow "
        "artifact root) so model artifacts resolve to durable object storage."
    ),
    "provider": (
        "Restore the live external providers (URLs, API-key secrets, auth "
        "status) until the runtime provider probe reports healthy."
    ),
    "mlflow": (
        "Publish/approve the MLflow model versions and point the 'production' "
        "alias at them (MLFLOW_TRACKING_URI registry)."
    ),
    "external-data": (
        "Run a real ingestion for the required providers; the deployed release "
        "has no populated, lineage-complete ingestion run to serve."
    ),
    "worker": (
        "Restore the worker runtime: the Cloud Run worker job and its Cloud "
        "Scheduler trigger must drain the durable queue to a terminal state."
    ),
    "audit": (
        "Restore the durable audit log (WORM sink + PostgreSQL audit tables); "
        "receipts must survive the request that produced them."
    ),
    "data-binding": (
        "A fixture/mock/seed surrogate reached a live response. Remove the "
        "fallback and rebind the runtime to live data."
    ),
}


def _load_surrogate_scanner() -> Callable[[Any], list[str]]:
    """Reuse the marker vocabulary owned by the live production data gate.

    The two gates must agree on what "fixture" looks like; keeping one
    definition prevents a marker that blocks one gate from passing the other.
    """
    spec = importlib.util.spec_from_file_location(
        "odp_check_live_production_data", LIVE_DATA_GATE
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load surrogate marker vocabulary from {LIVE_DATA_GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.find_surrogate_values


find_surrogate_values = _load_surrogate_scanner()


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    name: str
    detail: str
    dependency: str = "api-runtime"


@dataclass(frozen=True)
class GateConfig:
    api_url: str
    expected_sha: str
    bearer_token: str
    operator_role: str
    web_url: str = ""
    # The deploy env the runtime is expected to report back as
    # ``details.deploymentMode``. It mirrors ODP_DEPLOY_ENV, so `dev` and
    # `staging` are legitimate values; live-ness is asserted separately via
    # ``requireLiveData`` and the persistence/provider/data-binding checks.
    expected_deployment: str = ""
    operator_subject: str = "live-e2e-gate"
    operator_tenant: str = ""
    required_provider_ids: tuple[str, ...] = DEFAULT_REQUIRED_PROVIDER_IDS
    worker_probe_provider_id: str = ""
    worker_deadline_seconds: float = 600.0
    poll_interval_seconds: float = 10.0
    timeout: float = 20.0
    allow_http: bool = False

    @property
    def unknown_provider_ids(self) -> tuple[str, ...]:
        """Required ids the pinned registry mirror does not know (registry drift)."""

        return tuple(
            provider_id
            for provider_id in self.required_provider_ids
            if provider_id not in PROVIDER_CATEGORIES
        )

    @property
    def snapshot_provider_ids(self) -> tuple[str, ...]:
        """Required providers that can actually produce a persisted ingestion run."""

        return tuple(
            provider_id
            for provider_id in self.required_provider_ids
            if PROVIDER_CATEGORIES.get(provider_id) in SNAPSHOT_SCHEDULABLE_CATEGORIES
        )

    @property
    def enrichment_provider_ids(self) -> tuple[str, ...]:
        """Required providers proven only by the live readiness probe."""

        return tuple(
            provider_id
            for provider_id in self.required_provider_ids
            if provider_id not in self.snapshot_provider_ids
        )

    @property
    def probe_provider_id(self) -> str:
        # The worker probe enqueues an ``external-fetch`` job, so the provider it
        # names must be snapshot-schedulable. Defaulting to
        # ``required_provider_ids[0]`` only ever worked by alphabetical luck.
        if self.worker_probe_provider_id:
            return self.worker_probe_provider_id
        snapshot = self.snapshot_provider_ids
        return snapshot[0] if snapshot else ""


@dataclass
class HttpResponse:
    status: int
    payload: dict[str, Any] = field(default_factory=dict)
    location: str = ""
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> HttpResponse: ...


class WorkerDriver(Protocol):
    def drain(self) -> tuple[bool, str]: ...


def _normalize_origin(value: str, *, allow_http: bool) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    allowed = {"https"} | ({"http"} if allow_http else set())
    if (
        parsed.scheme not in allowed
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("URL must be a credential-free HTTPS origin")
    if not allow_http and parsed.hostname.lower() in FORBIDDEN_HOSTS:
        raise ValueError("URL must identify a deployed non-example host")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    ).rstrip("/")


def _safe_request_path(path: str) -> str:
    value = str(path or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or ".." in parsed.path.split("/")
    ):
        raise ValueError("gate request path must be an absolute-path reference")
    return urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))


class UrllibHttpClient:
    """Minimal JSON/redirect-aware client bound to one deployed origin."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        bearer_token: str,
        operator_role: str,
        operator_subject: str,
        operator_tenant: str,
        correlation_id: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._bearer_token = bearer_token
        self._operator_role = operator_role
        self._operator_subject = operator_subject
        self._operator_tenant = operator_tenant
        self._correlation_id = correlation_id

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "authorization": f"Bearer {self._bearer_token}",
            "x-operator-role": self._operator_role,
        }
        if self._operator_subject:
            headers["x-subject-id"] = self._operator_subject
        if self._operator_tenant:
            headers["x-tenant-id"] = self._operator_tenant
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> HttpResponse:
        try:
            safe_path = _safe_request_path(path)
        except ValueError as exc:
            return HttpResponse(status=0, error=str(exc))

        request_headers = {
            "accept": "application/json",
            "x-correlation-id": self._correlation_id,
        }
        if authenticated:
            request_headers.update(self._auth_headers())
        if headers:
            request_headers.update({key.lower(): value for key, value in headers.items()})

        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["content-type"] = "application/json"

        request = urllib.request.Request(  # noqa: S310 - validated deployed origin
            f"{self._base_url}{safe_path}",
            data=data,
            headers=request_headers,
            method=method.upper(),
        )
        opener = (
            urllib.request.build_opener()
            if follow_redirects
            else urllib.request.build_opener(_NoRedirect)
        )
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return _to_response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return _to_response(exc.code, exc.headers, exc.read())
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            return HttpResponse(status=0, error=f"{type(exc).__name__} for {safe_path}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        return None


def _to_response(status: int, headers: Any, raw: bytes) -> HttpResponse:
    location = ""
    try:
        location = str(headers.get("location") or "")
    except AttributeError:  # pragma: no cover - non-standard header container
        location = ""
    payload: dict[str, Any] = {}
    if raw:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, dict):
            payload = decoded
    return HttpResponse(status=status, payload=payload, location=location)


class CloudRunWorkerDriver:
    """Drain the durable queue by executing the deployed worker Cloud Run Job."""

    def __init__(
        self,
        *,
        job: str,
        region: str,
        project: str,
        max_jobs: int = 5,
        timeout: float = 900.0,
        runner: Callable[[Sequence[str], float], tuple[int, str]] | None = None,
    ) -> None:
        self._job = job
        self._region = region
        self._project = project
        self._max_jobs = max_jobs
        self._timeout = timeout
        self._runner = runner or _run_subprocess

    def drain(self) -> tuple[bool, str]:
        argv = [
            "gcloud",
            "run",
            "jobs",
            "execute",
            self._job,
            f"--region={self._region}",
            f"--project={self._project}",
            "--wait",
            "--quiet",
            (
                "--args=product_ops/deployment/cloud_run_job_entrypoint.py,worker,"
                f"--max-jobs,{self._max_jobs}"
            ),
        ]
        code, output = self._runner(argv, self._timeout)
        if code == 0:
            return True, f"worker job {self._job} execution completed"
        return False, f"worker job {self._job} execution exit={code}: {output.strip()[-500:]}"


class ScheduledWorkerDriver:
    """No explicit trigger: the deployed Cloud Scheduler cron owns the drain."""

    def drain(self) -> tuple[bool, str]:
        return True, "no explicit worker trigger configured; awaiting scheduled worker run"


def _run_subprocess(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return completed.returncode, f"{completed.stdout}\n{completed.stderr}"


def _redactor(*secret_values: str) -> Callable[[Any], str]:
    secrets = sorted({value for value in secret_values if value}, key=len, reverse=True)

    def redact(value: Any) -> str:
        text = str(value)
        for secret in secrets:
            text = text.replace(secret, "<redacted>")
        return re.sub(
            r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", text, flags=re.IGNORECASE
        )

    return redact


def _check(
    checks: list[CheckResult], ok: bool, name: str, detail: str, dependency: str
) -> None:
    checks.append(CheckResult(bool(ok), name, detail, dependency))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dependency_for(response: HttpResponse, default: str) -> str:
    """Name the dependency an operator would actually have to repair.

    Every authenticated surface the gate reads is behind a
    ``require_permission`` guard, and ``X-Operator-Role`` selects a console
    persona rather than widening grants -- so a 401/403 means the smoke
    principal is missing a platform role, not that the domain dependency behind
    the route is broken. Reporting a 403 from ``/learninghub/models`` as an
    ``mlflow`` blocker sends the operator to republish an alias that is already
    there, which is the opposite of the actionable failure this gate owes.
    """
    return "auth" if response.status in {401, 403} else default


def _failure_detail(response: HttpResponse, *, expected: str = "") -> str:
    """Describe a bad response including a bounded excerpt of its body.

    The excerpt is what usually names the failing dependency (a 503 envelope
    carries ``details[].dependency``), so it is worth carrying into the report.
    It is redacted before it is written or printed.
    """
    if response.failed:
        return response.error
    parts = [f"status={response.status}"]
    if expected:
        parts.append(f"(expected {expected})")
    if response.payload:
        parts.append(f"body={json.dumps(response.payload, sort_keys=True)[:400]}")
    return " ".join(parts)


def _declared_data_mode(payload: Mapping[str, Any]) -> str:
    """Read the declared data mode from whichever envelope shape carries it.

    The readiness probe nests it under ``modes``/``details``; the operator
    envelope declares it as ``meta.dataMode``. A gate that only knew the
    readiness shape would read ``""`` from a perfectly healthy operator
    response and block the release for a missing field rather than a missing
    dependency.
    """
    modes = _as_dict(payload.get("modes"))
    data = _as_dict(modes.get("data")) or _as_dict(_as_dict(payload.get("details")).get("data"))
    meta = _as_dict(payload.get("meta"))
    for candidate in (
        data.get("mode"),
        payload.get("data_mode"),
        payload.get("dataMode"),
        meta.get("dataMode"),
        meta.get("data_mode"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return ""


def _operator_source(payload: Mapping[str, Any]) -> str:
    for key in ("data_source", "dataSource", "source"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    meta = _as_dict(payload.get("meta")) or _as_dict(payload.get("provenance"))
    for key in ("data_source", "dataSource", "source", "origin"):
        candidate = meta.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def validate_config(config: GateConfig) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        _normalize_origin(config.api_url, allow_http=config.allow_http)
        _check(checks, True, "config:api_url", "configured deployed API origin", "config")
    except ValueError as exc:
        _check(checks, False, "config:api_url", str(exc), "config")

    # An empty --web-url used to silently drop the protected-route assertion
    # while the gate still reported ok. The web origin is part of the release.
    if not config.web_url:
        _check(checks, False, "config:web_url", f"missing {WEB_URL_ENV}", "config")
    else:
        try:
            _normalize_origin(config.web_url, allow_http=config.allow_http)
            _check(checks, True, "config:web_url", "configured deployed Web origin", "config")
        except ValueError as exc:
            _check(checks, False, "config:web_url", str(exc), "config")

    sha_ok = bool(SHA_PATTERN.fullmatch(config.expected_sha))
    _check(
        checks,
        sha_ok,
        "config:expected_sha",
        config.expected_sha if sha_ok else "missing/invalid 40-hex release SHA",
        "config",
    )
    _check(
        checks,
        bool(config.bearer_token),
        "config:operator_credential",
        "configured" if config.bearer_token else f"missing {BEARER_TOKEN_ENV}",
        "config",
    )
    _check(
        checks,
        bool(config.operator_role),
        "config:operator_role",
        config.operator_role or f"missing {OPERATOR_ROLE_ENV}",
        "config",
    )
    _check(
        checks,
        bool(config.required_provider_ids),
        "config:required_providers",
        ",".join(config.required_provider_ids) or "missing required provider ids",
        "config",
    )
    _check(
        checks,
        not config.unknown_provider_ids,
        "config:provider_registry_known",
        (
            "all required providers are classified"
            if not config.unknown_provider_ids
            else (
                f"unclassified={','.join(config.unknown_provider_ids)} "
                "(PROVIDER_CATEGORIES has drifted from provider_registry())"
            )
        ),
        "config",
    )
    # Without at least one snapshot-schedulable provider the ingestion-run
    # assertions would iterate an empty set and pass vacuously.
    _check(
        checks,
        bool(config.snapshot_provider_ids),
        "config:snapshot_providers",
        (
            ",".join(config.snapshot_provider_ids)
            or "no required provider can produce an ingestion run"
        ),
        "config",
    )
    _check(
        checks,
        bool(config.probe_provider_id)
        and config.probe_provider_id in config.snapshot_provider_ids,
        "config:worker_probe_provider",
        (
            f"probeProvider={config.probe_provider_id or '<missing>'} "
            f"schedulable={','.join(config.snapshot_provider_ids) or 'none'}"
        ),
        "config",
    )
    # The gate compares this against the runtime's self-reported
    # ``details.deploymentMode``. Empty means the caller never told the gate
    # which env it deployed, which would turn the assertion into a comparison
    # against "" rather than a real binding.
    _check(
        checks,
        bool(config.expected_deployment),
        "config:expected_deployment",
        (
            config.expected_deployment
            or "missing --expected-deployment (pass the deploy env, e.g. ODP_DEPLOY_ENV)"
        ),
        "config",
    )
    _check(
        checks,
        config.worker_deadline_seconds > 0 and config.poll_interval_seconds > 0,
        "config:worker_polling",
        (
            f"deadline={config.worker_deadline_seconds}s "
            f"interval={config.poll_interval_seconds}s"
        ),
        "config",
    )
    return checks


def _check_release_binding(
    response: HttpResponse, *, config: GateConfig, checks: list[CheckResult]
) -> None:
    if response.failed or response.status != 200:
        _check(
            checks,
            False,
            "release:platform_version",
            _failure_detail(response),
            "release",
        )
        return
    actual = str(response.payload.get("release_sha") or "").strip().lower()
    _check(
        checks,
        actual == config.expected_sha,
        "release:platform_version",
        f"expected={config.expected_sha} actual={actual or '<missing>'}",
        "release",
    )
    markers = find_surrogate_values(response.payload)
    _check(
        checks,
        not markers,
        "release:no_surrogate_markers",
        "none" if not markers else f"paths={markers[:10]}",
        "data-binding",
    )


def _check_runtime_readiness(
    response: HttpResponse, *, config: GateConfig, checks: list[CheckResult]
) -> None:
    if response.failed or response.status != 200:
        _check(
            checks,
            False,
            "runtime:readiness",
            _failure_detail(response),
            "api-runtime",
        )
        return

    payload = response.payload
    details = _as_dict(payload.get("details"))
    persistence = _as_dict(details.get("persistence"))
    provider = _as_dict(details.get("provider"))
    models = _as_dict(details.get("models"))
    data = _as_dict(details.get("data"))
    probe = _as_dict(data.get("operatorRepositoryProbe"))

    # ``deploymentMode`` binds the served runtime to the env this deploy
    # configured (ODP_DEPLOY_ENV), so a revision booted with someone else's env
    # fails. It is *not* the live-ness assertion -- ``requireLiveData`` is, and
    # the persistence/provider/model/data checks below carry the rest.
    reported_deployment = str(details.get("deploymentMode") or "").strip().lower()
    _check(
        checks,
        payload.get("status") == "ok"
        and details.get("requireLiveData") is True
        and bool(config.expected_deployment)
        and reported_deployment == config.expected_deployment,
        "runtime:readiness",
        (
            f"status={payload.get('status')} "
            f"requireLiveData={details.get('requireLiveData')} "
            f"deploymentMode={details.get('deploymentMode')} "
            f"expectedDeploymentMode={config.expected_deployment or '<missing>'}"
        ),
        "api-runtime",
    )
    _check(
        checks,
        str(persistence.get("configuredMode")).lower() in POSTGRES_MODES
        and str(persistence.get("runtimeMode")).lower() in POSTGRES_MODES
        and persistence.get("durable") is True
        and persistence.get("reachable") is True
        and persistence.get("production_persistence_supported") is True,
        "runtime:persistence",
        (
            f"configured={persistence.get('configuredMode')} "
            f"runtime={persistence.get('runtimeMode')} "
            f"durable={persistence.get('durable')} "
            f"reachable={persistence.get('reachable')}"
        ),
        "postgresql",
    )
    _check(
        checks,
        provider.get("mode") == "live"
        and provider.get("configurationValid") is True
        and provider.get("connectivityHealthy") is True
        and provider.get("live") is True,
        "runtime:provider",
        (
            f"mode={provider.get('mode')} "
            f"configurationValid={provider.get('configurationValid')} "
            f"connectivityHealthy={provider.get('connectivityHealthy')}"
        ),
        "provider",
    )
    _check(
        checks,
        models.get("mode") == "mlflow-production"
        and models.get("productionBindingsReady") is True
        and models.get("autoSeeded") is False
        and not models.get("error"),
        "runtime:model_bindings",
        (
            f"mode={models.get('mode')} "
            f"ready={models.get('productionBindingsReady')} "
            f"autoSeeded={models.get('autoSeeded')} "
            f"error={models.get('error') or 'none'}"
        ),
        "mlflow",
    )
    _check_provider_probe_evidence(provider, config=config, checks=checks)

    capabilities = _as_dict(models.get("capabilities"))
    for service in sorted(REQUIRED_MODEL_BINDINGS):
        capability = _as_dict(capabilities.get(service))
        if service in GOVERNED_DISABLED_SERVICES:
            # Governed-disabled: available must be False, and full evidence
            # must be present in governedDisabledEvidence.
            evidence = _as_dict(capability.get("governedDisabledEvidence"))
            reason_code = str(capability.get("reasonCode") or "").strip()
            evidence_reason = str(evidence.get("reasonCode") or "").strip()
            counts_valid = all(
                isinstance(evidence.get(field), int)
                and not isinstance(evidence.get(field), bool)
                and evidence[field] >= 0
                for field in ("observedCount", "eligibleCount")
            )
            activation_threshold = evidence.get("activationThreshold")
            activation_threshold_valid = (
                isinstance(activation_threshold, int)
                and not isinstance(activation_threshold, bool)
                and activation_threshold > 0
            )
            text_fields_valid = all(
                isinstance(evidence.get(field), str) and bool(evidence[field].strip())
                for field in _GOVERNED_DISABLED_TEXT_FIELDS
            )
            evidence_complete = (
                bool(reason_code)
                and capability.get("governedDisabled") is True
                and capability.get("available") is False
                # The evidence must describe the same fact as the capability
                # record; a diverging reasonCode means the evidence was authored
                # for a different (or stale) disablement decision.
                and evidence_reason == reason_code
                # autoSeeded=False in the evidence is a hard requirement;
                # True means synthetic/fixture data was substituted.
                and evidence.get("autoSeeded") is False
                # bool is an int subclass in Python, so reject it explicitly.
                # observedCount=0 remains valid receipt-backed evidence.
                and counts_valid
                and activation_threshold_valid
                and text_fields_valid
            )

            _check(
                checks,
                evidence_complete,
                f"runtime:model_capability:{service}",
                (
                    f"governedDisabled=True available={capability.get('available')} "
                    f"reasonCode={reason_code or '<missing>'} "
                    f"evidenceReasonCode={evidence_reason or '<missing>'} "
                    f"evidenceComplete={evidence_complete} "
                    f"evidenceAutoSeeded={evidence.get('autoSeeded')} "
                    f"inventoryVersion={evidence.get('inventoryVersion') or '<missing>'} "
                    f"observedAt={evidence.get('observedAt') or '<missing>'}"
                ),
                "mlflow",
            )
        else:
            # Active service (ForecastOps): must be available=True with no reasonCode.
            _check(
                checks,
                capability.get("available") is True and not capability.get("reasonCode"),
                f"runtime:model_capability:{service}",
                (
                    f"available={capability.get('available')} "
                    f"reasonCode={capability.get('reasonCode') or 'none'}"
                ),
                "mlflow",
            )

    origin = _as_dict(data.get("origin"))
    _check(
        checks,
        data.get("mode") == "live"
        and data.get("liveReady") is True
        and data.get("operatorRepositoryReady") is True
        and str(origin.get("kind") or "").lower() in LIVE_ORIGIN_KINDS
        and str(origin.get("persistenceMode")).lower() in POSTGRES_MODES
        and probe.get("ready") is True,
        "runtime:data_origin",
        (
            f"mode={data.get('mode')} origin={origin.get('kind')} "
            f"persistenceMode={origin.get('persistenceMode')} "
            f"operatorReady={data.get('operatorRepositoryReady')}"
        ),
        "postgresql",
    )
    blockers = data.get("blockingReasons")
    _check(
        checks,
        isinstance(blockers, list) and not blockers,
        "runtime:no_blocking_reasons",
        "none" if isinstance(blockers, list) and not blockers else f"reasons={blockers}",
        "api-runtime",
    )
    markers = find_surrogate_values(payload)
    _check(
        checks,
        not markers,
        "runtime:no_surrogate_markers",
        "none" if not markers else f"paths={markers[:10]}",
        "data-binding",
    )


def _check_provider_probe_evidence(
    provider: Mapping[str, Any], *, config: GateConfig, checks: list[CheckResult]
) -> None:
    """Assert per-provider live connectivity from the readiness probe evidence.

    ``details.provider.probeEvidence`` is ``ProviderConnectivityResult.to_dict()``:
    one entry per required provider, each recording whether an authenticated
    request reached the upstream and returned a schema-valid payload. For
    ``geocode.primary_api`` that probe is a real POST whose response coordinates
    are range-validated, which is the only surface that can prove geocode
    liveness -- geocode can never appear in the ingestion-run history.

    Freshness is intentionally not asserted against the gate runner's clock:
    ``/readiness`` recomputes any probe older than
    ``ODP_EXTERNAL_PROVIDER_PROBE_MAX_AGE_SECONDS``, so a healthy deployment
    always answers with a fresh probe, and a wall-clock comparison would only
    add a skew-driven way to fail a healthy release.
    """

    evidence = _as_dict(provider.get("probeEvidence"))
    probes = evidence.get("probes")
    probes = [row for row in probes if isinstance(row, dict)] if isinstance(probes, list) else []
    by_provider = {str(row.get("provider_id") or ""): row for row in probes}

    for provider_id in config.required_provider_ids:
        probe = by_provider.get(provider_id)
        if probe is None:
            _check(
                checks,
                False,
                f"runtime:provider_probe:{provider_id}",
                "readiness published no connectivity probe for a required live provider",
                "provider",
            )
            continue
        _check(
            checks,
            probe.get("connectivity_healthy") is True
            and probe.get("authentication_accepted") is True
            and probe.get("response_valid") is True
            and probe.get("schema_valid") is True
            and str(probe.get("reason_code") or "") == "ok",
            f"runtime:provider_probe:{provider_id}",
            (
                f"connectivityHealthy={probe.get('connectivity_healthy')} "
                f"authenticated={probe.get('authentication_accepted')} "
                f"schemaValid={probe.get('schema_valid')} "
                f"reasonCode={probe.get('reason_code') or '<missing>'}"
            ),
            "provider",
        )


def _check_authenticated_operator(
    *, http: HttpClient, config: GateConfig, checks: list[CheckResult]
) -> None:
    anonymous = http.request(
        "GET", "/api/v1/operator/bootstrap", authenticated=False
    )
    _check(
        checks,
        (not anonymous.failed) and anonymous.status in DENIED_STATUSES,
        "auth:anonymous_denied",
        (
            anonymous.error
            if anonymous.failed
            else f"status={anonymous.status} (expected 401/403)"
        ),
        "auth",
    )

    bootstrap = http.request("GET", "/api/v1/operator/bootstrap")
    if bootstrap.failed or bootstrap.status != 200:
        _check(
            checks,
            False,
            "auth:operator_bootstrap",
            _failure_detail(bootstrap, expected="200"),
            "auth",
        )
        return
    _check(checks, True, "auth:operator_bootstrap", "status=200", "auth")

    mode = _declared_data_mode(bootstrap.payload)
    source = _operator_source(bootstrap.payload)
    markers = find_surrogate_values(bootstrap.payload)
    _check(
        checks,
        mode == "live" and bool(source) and not markers,
        "auth:operator_bootstrap:provenance",
        (
            f"data_mode={mode or '<missing>'} data_source={source or '<missing>'} "
            f"surrogatePaths={markers[:5] if markers else 'none'}"
        ),
        "data-binding",
    )


def _check_web_login(
    *, http: HttpClient, checks: list[CheckResult]
) -> None:
    response = http.request(
        "GET", "/operator", authenticated=False, follow_redirects=False
    )
    redirected = (
        not response.failed
        and response.status in {302, 303, 307, 308}
        and "/login?" in response.location
        and "returnTo=" in response.location
    )
    _check(
        checks,
        redirected,
        "auth:web_operator_requires_login",
        (
            response.error
            if response.failed
            else f"status={response.status} location={response.location or '<missing>'}"
        ),
        "auth",
    )


def _check_model_lineage(
    response: HttpResponse, *, checks: list[CheckResult]
) -> None:
    if response.failed or response.status != 200:
        _check(
            checks,
            False,
            "models:registry",
            _failure_detail(response),
            _dependency_for(response, "mlflow"),
        )
        return

    items = response.payload.get("items")
    items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    _check(
        checks,
        bool(items),
        "models:registry",
        f"versions={len(items)}",
        "mlflow",
    )

    for service, model_name in sorted(REQUIRED_MODEL_BINDINGS.items()):
        if service in GOVERNED_DISABLED_SERVICES:
            # A governed-disabled service must NOT have a production alias.
            # The runtime capability record carries the evidence instead.
            # Having an alias for a governed-disabled service is itself a blocker
            # (it would mean a fabricated alias was published).
            aliased_gd = [
                item
                for item in items
                if str(item.get("model_name") or "") == model_name
                and PRODUCTION_ALIAS in [str(a) for a in (item.get("aliases") or [])]
            ]
            _check(
                checks,
                len(aliased_gd) == 0,
                f"models:{service}:no_fabricated_alias",
                (
                    "governed-disabled service must not have a production alias "
                    f"(model={model_name} found={len(aliased_gd)})"
                ),
                "mlflow",
            )
            continue

        aliased = [
            item
            for item in items
            if str(item.get("model_name") or "") == model_name
            and PRODUCTION_ALIAS in [str(alias) for alias in (item.get("aliases") or [])]
        ]
        _check(
            checks,
            len(aliased) == 1,
            f"models:{service}:production_alias",
            (
                f"model={model_name} versionsWithProductionAlias={len(aliased)} "
                "(exactly one required)"
            ),
            "mlflow",
        )
        if len(aliased) != 1:
            continue

        version = aliased[0]
        _check(
            checks,
            bool(version.get("version"))
            and bool(version.get("dataset_snapshot_id"))
            and bool(version.get("feature_schema_version"))
            and bool(version.get("approved_by"))
            and bool(version.get("approved_at")),
            f"models:{service}:lineage",
            (
                f"version={version.get('version') or '<missing>'} "
                f"datasetSnapshot={version.get('dataset_snapshot_id') or '<missing>'} "
                f"featureSchema={version.get('feature_schema_version') or '<missing>'} "
                f"approvedBy={version.get('approved_by') or '<missing>'}"
            ),
            "mlflow",
        )
        artifact_uri = str(version.get("artifact_uri") or "")
        scheme = urllib.parse.urlsplit(artifact_uri).scheme.lower()
        _check(
            checks,
            scheme in ARTIFACT_SCHEMES,
            f"models:{service}:artifact_store",
            f"artifactScheme={scheme or '<missing>'} (expected one of {sorted(ARTIFACT_SCHEMES)})",
            "object-store",
        )

    markers = find_surrogate_values(response.payload)
    _check(
        checks,
        not markers,
        "models:no_surrogate_markers",
        "none" if not markers else f"paths={markers[:10]}",
        "data-binding",
    )


def _latest_run_by_provider(items: Sequence[Any]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "")
        if not provider_id:
            continue
        current = latest.get(provider_id)
        if current is None or str(item.get("completed_at") or "") >= str(
            current.get("completed_at") or ""
        ):
            latest[provider_id] = item
    return latest


def _check_source_data(
    response: HttpResponse, *, config: GateConfig, checks: list[CheckResult]
) -> None:
    if response.failed or response.status != 200:
        _check(
            checks,
            False,
            "data:ingestion_runs",
            _failure_detail(response),
            _dependency_for(response, "external-data"),
        )
        return

    items = response.payload.get("items")
    items = items if isinstance(items, list) else []
    _check(
        checks,
        bool(items),
        "data:ingestion_runs",
        f"runs={len(items)}",
        "external-data",
    )

    latest = _latest_run_by_provider(items)
    # Only snapshot-schedulable providers can have an ingestion run at all; the
    # enrichment providers in the required set are proven by
    # ``runtime:provider_probe:*`` instead. See PROVIDER_CATEGORIES.
    for provider_id in config.snapshot_provider_ids:
        run = latest.get(provider_id)
        if run is None:
            _check(
                checks,
                False,
                f"data:{provider_id}:run_exists",
                "no persisted ingestion run for a required live provider",
                "external-data",
            )
            continue
        _check(
            checks,
            str(run.get("status") or "").upper() in {"SUCCEEDED", "COMPLETED"},
            f"data:{provider_id}:status",
            f"status={run.get('status') or '<missing>'}",
            "external-data",
        )

        total = int(run.get("total_count") or 0)
        accepted = int(run.get("accepted_count") or 0)
        quarantined = int(run.get("quarantined_count") or 0)
        _check(
            checks,
            total > 0 and accepted > 0 and accepted + quarantined == total,
            f"data:{provider_id}:row_counts",
            f"total={total} accepted={accepted} quarantined={quarantined}",
            "external-data",
        )

        lineage = run.get("lineage")
        lineage = [row for row in lineage if isinstance(row, dict)] if isinstance(lineage, list) else []
        accepted_lineage = [row for row in lineage if row.get("accepted") is True]
        complete_lineage = all(
            bool(row.get("contract_id"))
            and bool(row.get("source_system"))
            and bool(row.get("source_record_id"))
            and bool(row.get("canonical_target"))
            for row in lineage
        )
        _check(
            checks,
            len(lineage) == total
            and len(accepted_lineage) == accepted
            and complete_lineage,
            f"data:{provider_id}:lineage",
            (
                f"lineageRows={len(lineage)} expected={total} "
                f"acceptedLineage={len(accepted_lineage)} expectedAccepted={accepted} "
                f"provenanceComplete={complete_lineage}"
            ),
            "external-data",
        )

        snapshot_ids = run.get("source_snapshot_ids")
        snapshot_ids = snapshot_ids if isinstance(snapshot_ids, list) else []
        _check(
            checks,
            bool(snapshot_ids) and bool(run.get("canonical_snapshot_id")),
            f"data:{provider_id}:snapshot_binding",
            (
                f"sourceSnapshots={len(snapshot_ids)} "
                f"canonicalSnapshot={run.get('canonical_snapshot_id') or '<missing>'}"
            ),
            "object-store",
        )

    markers = find_surrogate_values(response.payload)
    _check(
        checks,
        not markers,
        "data:no_surrogate_markers",
        "none" if not markers else f"paths={markers[:10]}",
        "data-binding",
    )


def _enqueue_body(
    config: GateConfig, idempotency_key: str, *, provider_id: str = ""
) -> dict[str, Any]:
    """Build the probe enqueue body.

    The tenant is deliberately *not* guessed from the deployment environment.
    A probe that writes under one tenant while the gate reads back under the
    operator credential's own tenant is invisible: the worker reports success
    and ``data:*`` reports zero runs, which is exactly the split diagnosed in
    ODP-P10-LIVE-EXTDATA-DIAG-001. ``POST /api/v1/jobs`` now binds the
    ingestion tenant to the authenticated principal, so omitting the field
    makes the probe write wherever this same credential reads. An explicit
    ``--operator-tenant`` is still sent, so a stale override fails loudly with
    ``TENANT_SCOPE_MISMATCH`` instead of silently writing somewhere unreadable.
    """
    payload: dict[str, Any] = {
        "provider_id": provider_id or config.probe_provider_id,
        "schedule_id": "live-e2e-gate",
    }
    if config.operator_tenant:
        payload["tenant_id"] = config.operator_tenant
    return {
        "job_type": WORKER_PROBE_JOB_TYPE,
        "payload": payload,
        "idempotency_key": idempotency_key,
    }


def _await_terminal_status(
    *,
    http: HttpClient,
    job_id: str,
    status_value: str,
    config: GateConfig,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[str, str]:
    """Poll one job to a terminal state, returning (status, last detail)."""

    deadline = monotonic() + config.worker_deadline_seconds
    last_detail = f"status={status_value or '<missing>'}"
    while status_value not in TERMINAL_JOB_STATUSES and monotonic() < deadline:
        sleep(config.poll_interval_seconds)
        polled = http.request("GET", f"/api/v1/jobs/{urllib.parse.quote(job_id)}")
        if polled.failed or polled.status != 200:
            last_detail = polled.error or f"status={polled.status}"
            continue
        status_value = str(polled.payload.get("status") or "").lower()
        last_detail = (
            f"status={status_value or '<missing>'} "
            f"attempts={polled.payload.get('attempts')} "
            f"error={polled.payload.get('error_message') or 'none'}"
        )
    return status_value, last_detail


def _check_worker_and_audit(
    *,
    http: HttpClient,
    config: GateConfig,
    worker_driver: WorkerDriver,
    correlation_id: str,
    checks: list[CheckResult],
    report: dict[str, Any],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    idempotency_key = f"live-e2e-{config.expected_sha[:12]}-{correlation_id}"
    body = _enqueue_body(config, idempotency_key)
    headers = {"idempotency-key": idempotency_key}

    enqueue = http.request("POST", "/api/v1/jobs", body=body, headers=headers)
    if enqueue.failed or enqueue.status != 202:
        _check(
            checks,
            False,
            "worker:enqueue",
            _failure_detail(enqueue, expected="202"),
            "worker",
        )
        return

    job_id = str(enqueue.payload.get("job_id") or "")
    job = _as_dict(enqueue.payload.get("job"))
    audit_event_id = str(enqueue.payload.get("audit_event_id") or "")
    report["worker"] = {
        "job_id": job_id,
        "job_type": WORKER_PROBE_JOB_TYPE,
        "idempotency_key_configured": bool(idempotency_key),
    }
    _check(
        checks,
        bool(job_id)
        and str(job.get("status") or "").lower() in ACCEPTED_ENQUEUE_STATUSES
        and str(job.get("correlation_id") or "") == correlation_id
        and bool(audit_event_id),
        "worker:enqueue",
        (
            f"status={enqueue.status} jobId={'present' if job_id else 'missing'} "
            f"jobStatus={job.get('status') or '<missing>'} "
            f"auditEventId={'present' if audit_event_id else 'missing'}"
        ),
        "worker",
    )
    if not job_id:
        return

    replay = http.request("POST", "/api/v1/jobs", body=body, headers=headers)
    _check(
        checks,
        (not replay.failed)
        and replay.status == 202
        and str(replay.payload.get("job_id") or "") == job_id
        and replay.payload.get("created") is False,
        "worker:idempotent_replay",
        (
            replay.error
            if replay.failed
            else (
                f"status={replay.status} sameJob="
                f"{str(replay.payload.get('job_id') or '') == job_id} "
                f"created={replay.payload.get('created')}"
            )
        ),
        "worker",
    )

    # Every required snapshot provider needs a persisted ingestion run, and the
    # scheduled worker path is the only thing that writes one in a deployed
    # environment (the Cloud Scheduler cron only enqueues
    # ``listing.partner_feed``). So the gate enqueues the same ``external-fetch``
    # job for the remaining snapshot providers here, before the single drain,
    # instead of demanding history that nobody would have produced. This is what
    # makes ``_check_source_data`` assert real live ingestion rather than a
    # manual POST somebody had to remember.
    secondary_jobs: list[tuple[str, str, str]] = []
    for provider_id in config.snapshot_provider_ids:
        if provider_id == config.probe_provider_id:
            continue
        provider_key = f"{idempotency_key}-{provider_id}"
        response = http.request(
            "POST",
            "/api/v1/jobs",
            body=_enqueue_body(config, provider_key, provider_id=provider_id),
            headers={"idempotency-key": provider_key},
        )
        if response.failed or response.status != 202:
            _check(
                checks,
                False,
                f"worker:ingestion_probe:{provider_id}",
                _failure_detail(response, expected="202"),
                "worker",
            )
            continue
        provider_job_id = str(response.payload.get("job_id") or "")
        if not provider_job_id:
            _check(
                checks,
                False,
                f"worker:ingestion_probe:{provider_id}",
                "enqueue accepted without a job id",
                "worker",
            )
            continue
        secondary_jobs.append(
            (
                provider_id,
                provider_job_id,
                str(_as_dict(response.payload.get("job")).get("status") or "").lower(),
            )
        )

    drained, drain_detail = worker_driver.drain()
    _check(checks, drained, "worker:drain_trigger", drain_detail, "worker")

    status_value, last_detail = _await_terminal_status(
        http=http,
        job_id=job_id,
        status_value=str(job.get("status") or "").lower(),
        config=config,
        monotonic=monotonic,
        sleep=sleep,
    )

    _check(
        checks,
        status_value == "succeeded",
        "worker:terminal_success",
        f"{last_detail} deadline={config.worker_deadline_seconds}s",
        "worker",
    )
    if isinstance(report.get("worker"), dict):
        report["worker"]["terminal_status"] = status_value or None
        report["worker"]["ingestion_probe_provider_ids"] = list(config.snapshot_provider_ids)

    for provider_id, provider_job_id, initial_status in secondary_jobs:
        provider_status, provider_detail = _await_terminal_status(
            http=http,
            job_id=provider_job_id,
            status_value=initial_status,
            config=config,
            monotonic=monotonic,
            sleep=sleep,
        )
        _check(
            checks,
            provider_status == "succeeded",
            f"worker:ingestion_probe:{provider_id}",
            f"{provider_detail} deadline={config.worker_deadline_seconds}s",
            "worker",
        )

    events_response = http.request(
        "GET",
        f"/api/v1/audit/events?correlation_id={urllib.parse.quote(correlation_id)}",
    )
    if events_response.failed or events_response.status != 200:
        _check(
            checks,
            False,
            "audit:durable_receipt",
            _failure_detail(events_response, expected="200"),
            _dependency_for(events_response, "audit"),
        )
        return

    events = events_response.payload.get("events")
    events = [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []
    enqueue_events = [
        event
        for event in events
        if event.get("event_type") == "job.enqueue" and str(event.get("job_id") or "") == job_id
    ]
    accepted = [event for event in enqueue_events if event.get("outcome") == "accepted"]
    replayed = [event for event in enqueue_events if event.get("outcome") == "idempotent_replay"]
    _check(
        checks,
        bool(accepted),
        "audit:durable_receipt",
        (
            f"correlationId={correlation_id} jobEnqueueEvents={len(enqueue_events)} "
            f"accepted={len(accepted)}"
        ),
        "audit",
    )
    _check(
        checks,
        bool(replayed),
        "audit:idempotent_replay_receipt",
        f"idempotentReplayEvents={len(replayed)}",
        "audit",
    )
    integrity = [
        event
        for event in accepted
        if _as_dict(event.get("integrity")).get("event_hash")
        and _as_dict(event.get("integrity")).get("sequence") is not None
    ]
    _check(
        checks,
        bool(integrity),
        "audit:receipt_integrity",
        (
            "hash-chained receipt present"
            if integrity
            else "audit receipt carries no sequence/event_hash integrity envelope"
        ),
        "audit",
    )
    markers = find_surrogate_values(events_response.payload)
    _check(
        checks,
        not markers,
        "audit:no_surrogate_markers",
        "none" if not markers else f"paths={markers[:10]}",
        "data-binding",
    )


def evaluate_gate(
    config: GateConfig,
    *,
    http: HttpClient,
    worker_driver: WorkerDriver,
    correlation_id: str,
    now: str,
    web_http: HttpClient | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[CheckResult], dict[str, Any]]:
    redact = _redactor(config.bearer_token)
    checks = validate_config(config)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": now,
        "expected_release_sha": config.expected_sha,
        "expected_deployment": config.expected_deployment,
        "correlation_id": correlation_id,
        "inputs": {
            "api_url_configured": bool(config.api_url),
            "web_url_configured": bool(config.web_url),
            "operator_credential_configured": bool(config.bearer_token),
            "required_provider_ids": list(config.required_provider_ids),
            "snapshot_provider_ids": list(config.snapshot_provider_ids),
            "enrichment_provider_ids": list(config.enrichment_provider_ids),
            "worker_probe_provider_id": config.probe_provider_id,
            "secret_values_redacted": True,
        },
    }

    if all(check.ok for check in checks):
        _check_release_binding(
            http.request("GET", "/platform/version", authenticated=False),
            config=config,
            checks=checks,
        )
        _check_runtime_readiness(
            http.request("GET", "/readiness", authenticated=False),
            config=config,
            checks=checks,
        )
        _check_authenticated_operator(http=http, config=config, checks=checks)
        # A missing/unusable web client is a blocker, not a silently skipped
        # check: the protected-route assertion is part of the release contract.
        if web_http is None:
            _check(
                checks,
                False,
                "auth:web_operator_requires_login",
                f"no usable web origin for {config.web_url or '<missing>'}",
                "config",
            )
        else:
            _check_web_login(http=web_http, checks=checks)
        _check_model_lineage(
            http.request("GET", "/api/v1/learninghub/models"), checks=checks
        )
        # Worker first, then source data: the worker probe is what drives real
        # ingestion for every required snapshot provider through the deployed
        # scheduled path, and the ingestion runs it persists are exactly what
        # ``_check_source_data`` reads back. Reading the history first meant the
        # gate asserted evidence that only a manual POST could ever have created.
        _check_worker_and_audit(
            http=http,
            config=config,
            worker_driver=worker_driver,
            correlation_id=correlation_id,
            checks=checks,
            report=report,
            monotonic=monotonic,
            sleep=sleep,
        )
        _check_source_data(
            http.request("GET", "/api/v1/external-data/ingestion-runs?limit=100"),
            config=config,
            checks=checks,
        )

    blockers = [
        {
            "check": check.name,
            "dependency": check.dependency,
            "detail": redact(check.detail),
            "next_action": DEPENDENCY_ACTIONS.get(check.dependency, "Investigate the runtime."),
        }
        for check in checks
        if not check.ok
    ]
    report["ok"] = not blockers
    report["checks"] = [
        {**asdict(check), "detail": redact(check.detail)} for check in checks
    ]
    report["blockers"] = blockers
    report["blocking_dependencies"] = sorted({blocker["dependency"] for blocker in blockers})
    return checks, report


def _web_client(config: GateConfig, correlation_id: str) -> HttpClient | None:
    if not config.web_url:
        return None
    try:
        base = _normalize_origin(config.web_url, allow_http=config.allow_http)
    except ValueError:
        return None
    return UrllibHttpClient(
        base,
        timeout=config.timeout,
        bearer_token="",
        operator_role="",
        operator_subject="",
        operator_tenant="",
        correlation_id=correlation_id,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get(API_URL_ENV, ""))
    parser.add_argument("--web-url", default=os.environ.get(WEB_URL_ENV, ""))
    parser.add_argument("--expected-sha", default=os.environ.get(EXPECTED_SHA_ENV, ""))
    # No default: ``deploymentMode`` is the *deploy env* the runtime reports
    # (ODP_DEPLOY_ENV -> runtime_mode.deployment_mode()), not the product mode.
    # A hardcoded "production" default silently failed every non-prod deploy, so
    # the caller must state which env it deployed.
    parser.add_argument("--expected-deployment", default="")
    parser.add_argument(
        "--bearer-token-env",
        default=BEARER_TOKEN_ENV,
        help="Environment variable holding the operator bearer token; never printed.",
    )
    parser.add_argument("--operator-role", default=os.environ.get(OPERATOR_ROLE_ENV, ""))
    parser.add_argument("--operator-subject", default="live-e2e-gate")
    parser.add_argument("--operator-tenant", default="")
    parser.add_argument(
        "--required-provider",
        action="append",
        default=None,
        help=(
            "Provider id required in live mode (repeatable). Snapshot-schedulable "
            "ids must also have a populated ingestion run; enrichment ids are "
            "proven by the readiness connectivity probe."
        ),
    )
    parser.add_argument(
        "--worker-probe-provider",
        default="",
        help=(
            "Provider the external-fetch worker probe enqueues. Must be "
            "snapshot-schedulable; defaults to the first schedulable required id."
        ),
    )
    parser.add_argument("--worker-job", default="")
    parser.add_argument("--gcp-region", default=os.environ.get("GCP_REGION", ""))
    parser.add_argument("--gcp-project", default=os.environ.get("GCP_PROJECT", ""))
    parser.add_argument("--worker-deadline-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-http",
        action="store_true",
        help="Permit HTTP only for an explicitly controlled non-production target.",
    )
    return parser.parse_args(argv)


def _required_providers(args: argparse.Namespace) -> tuple[str, ...]:
    if args.required_provider:
        return tuple(sorted({value.strip() for value in args.required_provider if value.strip()}))
    declared = os.environ.get(PRODUCTION_PROVIDER_IDS_ENV, "")
    parsed = tuple(
        sorted({value.strip() for value in re.split(r"[,\s]+", declared) if value.strip()})
    )
    return parsed or DEFAULT_REQUIRED_PROVIDER_IDS


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = GateConfig(
        api_url=args.api_url.strip(),
        expected_sha=args.expected_sha.strip().lower(),
        bearer_token=os.environ.get(args.bearer_token_env, "").strip(),
        operator_role=args.operator_role.strip(),
        web_url=args.web_url.strip(),
        expected_deployment=args.expected_deployment.strip().lower(),
        operator_subject=args.operator_subject.strip(),
        operator_tenant=args.operator_tenant.strip(),
        required_provider_ids=_required_providers(args),
        worker_probe_provider_id=args.worker_probe_provider.strip(),
        worker_deadline_seconds=args.worker_deadline_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        timeout=args.timeout,
        allow_http=args.allow_http,
    )
    correlation_id = f"corr-live-e2e-{config.expected_sha[:12] or 'unbound'}-{int(time.time())}"

    try:
        api_base = _normalize_origin(config.api_url, allow_http=config.allow_http)
    except ValueError:
        api_base = ""

    http: HttpClient = UrllibHttpClient(
        api_base or "https://invalid.invalid",
        timeout=config.timeout,
        bearer_token=config.bearer_token,
        operator_role=config.operator_role,
        operator_subject=config.operator_subject,
        operator_tenant=config.operator_tenant,
        correlation_id=correlation_id,
    )
    if args.worker_job and args.gcp_region and args.gcp_project:
        worker_driver: WorkerDriver = CloudRunWorkerDriver(
            job=args.worker_job,
            region=args.gcp_region,
            project=args.gcp_project,
            # One drain must be able to clear every job the gate enqueues: the
            # lifecycle probe plus one ingestion probe per snapshot provider,
            # with headroom for anything the scheduler queued meanwhile.
            max_jobs=len(config.snapshot_provider_ids) + 4,
            timeout=max(config.worker_deadline_seconds, 60.0),
        )
    else:
        worker_driver = ScheduledWorkerDriver()

    _, report = evaluate_gate(
        config,
        http=http,
        worker_driver=worker_driver,
        correlation_id=correlation_id,
        now=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        web_http=_web_client(config, correlation_id),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if report["ok"]:
        print(f"Live E2E gate passed. report={args.output}")
        return 0

    print("Live E2E gate failed. Blocking runtime dependencies:")
    for dependency in report["blocking_dependencies"]:
        print(f"* {dependency}: {DEPENDENCY_ACTIONS.get(dependency, 'Investigate the runtime.')}")
        for blocker in report["blockers"]:
            if blocker["dependency"] == dependency:
                print(f"  - {blocker['check']}: {blocker['detail']}")
    print(f"report={args.output}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
