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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".odp_data" / "live-e2e-gate" / "live-e2e-gate-report.json"
LIVE_DATA_GATE = ROOT / "delivery_toolchain" / "e2e" / "check_live_production_data.py"
# ODP-BUSINESS-LIVE-E2E-COVERAGE-001: the six real business journeys whose
# sealed receipt is the only thing that may turn a passing ``full`` runtime
# gate into *full product acceptance*.
BUSINESS_JOURNEYS = ROOT / "delivery_toolchain" / "e2e" / "live_business_journeys.py"
BUSINESS_JOURNEY_RECEIPT_ENV = "ODP_LIVE_BUSINESS_JOURNEY_RECEIPT"
MANIFEST_DIGEST_ENV = "ODP_RELEASE_MANIFEST_DIGEST"

API_URL_ENV = "ODP_LIVE_E2E_API_URL"
WEB_URL_ENV = "ODP_LIVE_E2E_WEB_URL"
EXPECTED_SHA_ENV = "ODAY_RELEASE_SHA"
BEARER_TOKEN_ENV = "ODP_OPERATOR_SMOKE_BEARER_TOKEN"
# ODP-DEV-PRIVATE-API-TRANSPORT-AUTH-001: the API Cloud Run service is private.
# The deploy mints this Cloud Run transport ID token for the API's stable
# service URL; it rides in X-Serverless-Authorization on every API request,
# anonymous ones included, and never reaches the Web origin.
API_TRANSPORT_TOKEN_ENV = "ODP_API_INVOKER_TOKEN"
OPERATOR_ROLE_ENV = "ODP_OPERATOR_SMOKE_ROLE"
PRODUCTION_PROVIDER_IDS_ENV = "ODP_PRODUCTION_PROVIDER_IDS"
RELEASE_PROFILE_ENV = "ODP_RELEASE_PROFILE"
DEV_ADMIN_USERNAME_ENV = "ODP_DEV_ADMIN_USERNAME"
DEV_ADMIN_PASSWORD_ENV = "ODP_DEV_ADMIN_PASSWORD"
DEV_ADMIN_INITIAL_PASSWORD_ENV = "ODP_DEV_ADMIN_INITIAL_PASSWORD"
DEV_ADMIN_DENIED_ROLE_ENV = "ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE"
# The deployment-bootstrapped first administrator (platform_admin only). Proves
# the single pure administrator created by bootstrap can administer users and
# perform first-login password rotation without any business role.
BOOTSTRAP_ADMIN_USERNAME_ENV = "ODP_DEV_BOOTSTRAP_ADMIN_USERNAME"
BOOTSTRAP_ADMIN_PASSWORD_ENV = "ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD"
BOOTSTRAP_SECRET_ENV = "ODP_IDENTITY_BOOTSTRAP_SECRET"
BOOTSTRAP_AUDIT_EVENT = "identity.account.bootstrap"

# ODP-DEV-ADMIN-RELEASE-READINESS-001: the acceptance scope this gate holds a
# release to. It mirrors ``release_manifest.RELEASE_PROFILES`` (pinned, not
# imported, for the same reason as the provider maps below; the anti-drift
# suite binds them). ``full`` is the complete product contract and keeps every
# model assertion. ``dev-admin`` is dev-only: it replaces the "models must be
# production-ready" assertions with "missing models must be truthfully refused"
# and adds a real password sign-in/session/sign-out journey through the Web.
RELEASE_PROFILE_FULL = "full"
RELEASE_PROFILE_DEV_ADMIN = "dev-admin"
RELEASE_PROFILE_DEPLOYMENTS: Mapping[str, frozenset[str] | None] = {
    RELEASE_PROFILE_FULL: None,
    RELEASE_PROFILE_DEV_ADMIN: frozenset({"dev"}),
}
#: Runtime blocking reason the API publishes while production model bindings
#: are not verified; a dev-admin release must say so rather than hide it.
MODEL_BINDINGS_UNVERIFIED_REASON = "PRODUCTION_MODEL_BINDINGS_UNVERIFIED"
#: A well-formed tenant UUID no real operator account is issued. The dev-admin
#: journey asks the API to move the admin's own scope under it and requires the
#: identity tenant policy (not DTO/CSRF validation) to refuse. It must be a
#: valid UUID: a malformed id is rejected for being malformed, which proves
#: nothing about isolation.
FOREIGN_TENANT_PROBE_ID = "0d1e5a7e-f0e1-4000-8000-00000000f0e1"
#: Identity tenant-policy refusal the API returns (422) for a foreign scope.
TENANT_POLICY_REFUSAL_TEXT = "caller is restricted to its own tenant"
TENANT_SCOPE_MISMATCH_CODE = "TENANT_SCOPE_MISMATCH"

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
DISABLED_WORKER_PROBE_PROVIDER_ID = "listing.partner_feed"
#: Mirrors ``scheduled_fetch.PROVIDER_MODE_DISABLED_REASON_CODE``. This gate is
#: a standalone script that runs against a deployed release without importing
#: the application, so the shared contract is the string itself; the value is
#: pinned by tests/e2e/test_live_e2e_gate.py against the module constant.
PROVIDER_MODE_DISABLED_REASON_CODE = "provider_mode_disabled"

DEPENDENCY_ACTIONS: Mapping[str, str] = {
    "config": (
        "Set the gate inputs (API URL, web URL, release SHA, operator bearer "
        "token, API transport token, role, ODP_PRODUCTION_PROVIDER_IDS) in the "
        "deploy workflow environment."
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
    "session": (
        "Repair the Web password sign-in path: the provisioned operator account "
        "(ODP_DEV_ADMIN_USERNAME/ODP_DEV_ADMIN_PASSWORD), the durable Web "
        "session store and login throttle in PostgreSQL, and the session "
        "secret/identity signing key shared by Web and API."
    ),
    "tenant-isolation": (
        "A request outside the signed-in account's tenant was not refused. "
        "Restore tenant binding on the API (TENANT_SCOPE_MISMATCH) before any "
        "release."
    ),
    "release-profile": (
        "Redeploy from the admitted manifest: the runtime must report exactly "
        "the release profile sealed into the manifest digest, and a narrowed "
        "profile may only serve the deployment it was admitted for."
    ),
    "business-journey": (
        "Full product acceptance needs the sealed six-journey business receipt "
        "(delivery_toolchain/e2e/live_business_journeys.py) for this exact release "
        "and manifest digest, with every journey PASSED."
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


def _load_business_journeys() -> Any:
    """Load the six-journey receipt verifier (offline code, no runtime import)."""
    spec = importlib.util.spec_from_file_location(
        "odp_live_business_journeys", BUSINESS_JOURNEYS
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load business journeys from {BUSINESS_JOURNEYS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


business_journeys = _load_business_journeys()


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
    external_provider_mode: str = "live"
    worker_probe_provider_id: str = ""
    worker_deadline_seconds: float = 600.0
    poll_interval_seconds: float = 10.0
    timeout: float = 20.0
    allow_http: bool = False
    # Cloud Run transport identity for the private API service. It is a
    # different credential from ``bearer_token`` (the application token) and is
    # never used to decide whether a request is application-authenticated.
    api_transport_token: str = ""
    # The admitted acceptance scope (see RELEASE_PROFILE_DEPLOYMENTS).
    release_profile: str = RELEASE_PROFILE_FULL
    # dev-admin only: the bootstrap-created pure platform_admin account the gate
    # signs in as through the Web password form. Never written to the report.
    dev_admin_username: str = ""
    dev_admin_password: str = field(default="", repr=False)
    dev_admin_initial_password: str = field(default="", repr=False)
    # Non-secret expectations from a strictly parsed matched bundle. The gate
    # binds these to authoritative reads; they confer no permissions/provenance.
    dev_admin_bundle_account_id: str = ""
    dev_admin_bundle_tenant_id: str = ""
    dev_admin_bundle_execution_id: str = ""
    dev_admin_denied_role: str = ""
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    # ODP-BUSINESS-LIVE-E2E-COVERAGE-001: the admitted manifest digest the
    # deployment exported (ODP_RELEASE_MANIFEST_DIGEST). Full product
    # acceptance requires the six-journey receipt to be bound to it.
    expected_manifest_digest: str = ""

    @property
    def dev_admin(self) -> bool:
        return self.release_profile == RELEASE_PROFILE_DEV_ADMIN

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
        if snapshot:
            return snapshot[0]
        if self.external_provider_mode.strip().lower() == "disabled":
            # The disabled-mode probe is deliberately a known, schedulable
            # provider name. The worker records a BLOCKED receipt before any
            # factory/credential access; it is not an activation request.
            return DISABLED_WORKER_PROBE_PROVIDER_ID
        return ""


@dataclass
class HttpResponse:
    status: int
    payload: dict[str, Any] = field(default_factory=dict)
    location: str = ""
    error: str = ""
    # name -> value of every ``Set-Cookie`` on the response. An empty value is
    # a deletion. Cookies are only ever sent back explicitly by the caller.
    cookies: dict[str, str] = field(default_factory=dict)

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
        transport_token: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._bearer_token = bearer_token
        self._operator_role = operator_role
        self._operator_subject = operator_subject
        self._operator_tenant = operator_tenant
        self._correlation_id = correlation_id
        self._transport_token = transport_token

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
        # ``authenticated`` is about the application only: the Cloud Run
        # transport identity is sent either way, so an anonymous request is
        # denied (or not) by the application rather than by Cloud Run IAM.
        if authenticated:
            request_headers.update(self._auth_headers())
        if headers:
            request_headers.update({key.lower(): value for key, value in headers.items()})
        if self._transport_token:
            request_headers["x-serverless-authorization"] = f"Bearer {self._transport_token}"

        data: bytes | None = None
        if body is not None:
            if request_headers.get("content-type", "").split(";", 1)[0] == "application/x-www-form-urlencoded":
                data = urllib.parse.urlencode(body).encode("utf-8")
            else:
                data = json.dumps(body).encode("utf-8")
                request_headers["content-type"] = "application/json"

        request = urllib.request.Request(  # noqa: S310 - validated deployed origin
            f"{self._base_url}{safe_path}",
            data=data,
            headers=request_headers,
            method=method.upper(),
        )
        # urllib copies every header except the body ones onto a redirect
        # target, so following one off this origin would hand the transport
        # token (and the application bearer) to another host.
        opener = (
            urllib.request.build_opener(_SameOriginRedirect(self._base_url))
            if follow_redirects
            else urllib.request.build_opener(_NoRedirect)
        )
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return _to_response(response.status, response.headers, response.read())
        except _CrossOriginRedirectRefused:
            return HttpResponse(
                status=0, error=f"refused cross-origin redirect for {safe_path}"
            )
        except urllib.error.HTTPError as exc:
            return _to_response(exc.code, exc.headers, exc.read())
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            return HttpResponse(status=0, error=f"{type(exc).__name__} for {safe_path}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        return None


class _CrossOriginRedirectRefused(urllib.error.URLError):
    """A redirect left the client's origin; the target is deliberately not kept."""


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port
    except ValueError:
        return scheme, "", None
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, (parsed.hostname or "").lower(), port


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, base_url: str) -> None:
        self._origin = _origin(base_url)

    def redirect_request(  # noqa: D102
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        if _origin(newurl) != self._origin:
            raise _CrossOriginRedirectRefused("cross-origin redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    return HttpResponse(
        status=status, payload=payload, location=location, cookies=_set_cookies(headers)
    )


def _set_cookies(headers: Any) -> dict[str, str]:
    try:
        values = headers.get_all("set-cookie") or []
    except AttributeError:  # pragma: no cover - non-standard header container
        values = []
    cookies: dict[str, str] = {}
    for value in values:
        name, sep, rest = str(value).split(";", 1)[0].partition("=")
        if sep and name.strip():
            cookies[name.strip()] = rest.strip()
    return cookies


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
    # Without it every API request is refused by Cloud Run IAM, and that 403
    # must never stand in for the application's own anonymous denial.
    _check(
        checks,
        bool(config.api_transport_token),
        "config:api_transport_credential",
        "configured" if config.api_transport_token else f"missing {API_TRANSPORT_TOKEN_ENV}",
        "config",
    )
    _check(
        checks,
        bool(config.operator_role),
        "config:operator_role",
        config.operator_role or f"missing {OPERATOR_ROLE_ENV}",
        "config",
    )
    provider_mode = config.external_provider_mode.strip().lower()
    providers_disabled = provider_mode == "disabled" and not config.required_provider_ids
    _check(
        checks,
        provider_mode in {"live", "disabled"},
        "config:external_provider_mode",
        provider_mode or "missing external provider mode",
        "config",
    )
    _check(
        checks,
        providers_disabled or bool(config.required_provider_ids),
        "config:required_providers",
        "external providers disabled; no activation allowlist is expected"
        if providers_disabled
        else ",".join(config.required_provider_ids),
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
        providers_disabled or bool(config.snapshot_provider_ids),
        "config:snapshot_providers",
        "skipped while external providers are disabled"
        if providers_disabled
        else (",".join(config.snapshot_provider_ids) or "no required provider can produce an ingestion run"),
        "config",
    )
    _check(
        checks,
        providers_disabled
        or (bool(config.probe_provider_id) and config.probe_provider_id in config.snapshot_provider_ids),
        "config:worker_probe_provider",
        "skipped while external providers are disabled"
        if providers_disabled
        else (
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
    checks.extend(_release_profile_config_checks(config))
    return checks


def _release_profile_config_checks(config: GateConfig) -> list[CheckResult]:
    """Refuse an unknown profile, or a narrowed one outside its deployments.

    This runs before any request is made: a dev-admin scope pointed at staging
    or production is a configuration error, not something to discover live.
    """

    checks: list[CheckResult] = []
    profile = config.release_profile
    known = profile in RELEASE_PROFILE_DEPLOYMENTS
    _check(
        checks,
        known,
        "config:release_profile",
        profile if known else f"unknown release profile {profile!r}",
        "release-profile",
    )
    if not known:
        return checks
    allowed = RELEASE_PROFILE_DEPLOYMENTS[profile]
    if allowed is not None:
        _check(
            checks,
            config.expected_deployment in allowed,
            "config:release_profile_deployment",
            (
                f"profile={profile} expectedDeployment="
                f"{config.expected_deployment or '<missing>'} allowed={sorted(allowed)}"
            ),
            "release-profile",
        )
    if config.dev_admin:
        resolved_username = config.dev_admin_username or config.bootstrap_admin_username
        resolved_password = config.dev_admin_password or config.bootstrap_admin_password
        _check(
            checks,
            bool(resolved_username) and bool(resolved_password),
            "config:dev_admin_account",
            (
                "configured"
                if resolved_username and resolved_password
                else f"missing {DEV_ADMIN_USERNAME_ENV}/{DEV_ADMIN_PASSWORD_ENV}"
            ),
            "config",
        )
        _check(
            checks,
            bool(config.dev_admin_denied_role),
            "config:dev_admin_denied_role",
            config.dev_admin_denied_role or f"missing {DEV_ADMIN_DENIED_ROLE_ENV}",
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


def _disabled_provider_runtime_confirmed(
    config: GateConfig, provider: Mapping[str, Any]
) -> bool:
    """Return true only when the served runtime proves consumer-only mode."""

    return (
        config.external_provider_mode.strip().lower() == "disabled"
        and not config.required_provider_ids
        and str(provider.get("mode") or "").strip().lower() == "disabled"
        and provider.get("configurationValid") is True
        and provider.get("connectivityHealthy") is False
        and provider.get("live") is False
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
    runtime_profile = _as_dict(details.get("releaseProfile"))
    expected_claim = config.release_profile == RELEASE_PROFILE_FULL
    _check(
        checks,
        runtime_profile.get("name") == config.release_profile
        and runtime_profile.get("valid") is True
        and runtime_profile.get("modelReadinessClaimed") is expected_claim,
        "runtime:release_profile",
        (
            f"admitted={config.release_profile} "
            f"runtime={runtime_profile.get('name') or '<missing>'} "
            f"valid={runtime_profile.get('valid')} "
            f"modelReadinessClaimed={runtime_profile.get('modelReadinessClaimed')} "
            f"error={runtime_profile.get('error') or 'none'}"
        ),
        "release-profile",
    )
    provider_disabled = _disabled_provider_runtime_confirmed(config, provider)
    _check(
        checks,
        str(provider.get("mode") or "").strip().lower()
        == config.external_provider_mode.strip().lower(),
        "runtime:provider_mode_alignment",
        (
            f"configured={config.external_provider_mode or '<missing>'} "
            f"runtime={provider.get('mode') or '<missing>'}"
        ),
        "provider",
    )
    _check(
        checks,
        provider_disabled
        or (
            provider.get("mode") == "live"
            and provider.get("configurationValid") is True
            and provider.get("connectivityHealthy") is True
            and provider.get("live") is True
        ),
        "runtime:provider",
        (
            f"mode={provider.get('mode')} "
            f"configurationValid={provider.get('configurationValid')} "
            f"connectivityHealthy={provider.get('connectivityHealthy')}"
        ),
        "provider",
    )
    if _models_refused_in_scope(config, models):
        _check_model_limitation(models, checks=checks)
    else:
        _check_model_bindings(models, checks=checks)
    _check_provider_probe_evidence(provider, config=config, checks=checks)
    _check_model_capabilities(
        models, refused_in_scope=_models_refused_in_scope(config, models), checks=checks
    )
    _check_data_origin(payload, data=data, probe=probe, checks=checks)


def _models_refused_in_scope(config: GateConfig, models: Mapping[str, Any]) -> bool:
    """True when this scope accepts, and therefore must verify, absent models.

    Only a dev-admin release whose runtime does not claim production bindings
    takes this branch. A dev-admin release that *does* have verified bindings is
    held to the full model assertions, and a full release always is.
    """

    return config.dev_admin and models.get("productionBindingsReady") is not True


def _check_model_limitation(
    models: Mapping[str, Any], *, checks: list[CheckResult]
) -> None:
    """Missing production models must be reported, not papered over.

    The runtime must say bindings are not ready, name why, carry the
    ``PRODUCTION_MODEL_BINDINGS_UNVERIFIED`` blocking reason, and must not have
    substituted seeded models or claimed the ``mlflow-production`` mode.
    """

    reasons = models.get("blockingReasons")
    reasons = reasons if isinstance(reasons, list) else []
    _check(
        checks,
        models.get("productionBindingsReady") is False
        and models.get("autoSeeded") is False
        and models.get("mode") != "mlflow-production"
        and bool(str(models.get("error") or "").strip())
        and MODEL_BINDINGS_UNVERIFIED_REASON in reasons,
        "runtime:model_limitation_reported",
        (
            f"mode={models.get('mode')} ready={models.get('productionBindingsReady')} "
            f"autoSeeded={models.get('autoSeeded')} "
            f"error={'present' if str(models.get('error') or '').strip() else '<missing>'} "
            f"blockingReasons={reasons}"
        ),
        "mlflow",
    )


def _check_model_bindings(
    models: Mapping[str, Any], *, checks: list[CheckResult]
) -> None:
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


def _check_model_capabilities(
    models: Mapping[str, Any], *, refused_in_scope: bool, checks: list[CheckResult]
) -> None:
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
        elif refused_in_scope:
            # dev-admin with absent models: an active service is either really
            # bound, or truthfully unavailable with the runtime's reason code
            # (the composition error is asserted once, on ``models.error``). A
            # capability that is neither -- unavailable with no reason, or
            # "available" with a reason -- is a manufactured state.
            available = capability.get("available")
            reason_code = str(capability.get("reasonCode") or "").strip()
            truthful = (available is True and not reason_code) or (
                available is False and bool(reason_code)
            )
            _check(
                checks,
                truthful,
                f"runtime:model_capability:{service}",
                (
                    f"available={available} reasonCode={reason_code or '<missing>'} "
                    "(refused-in-scope)"
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


def _check_data_origin(
    payload: Mapping[str, Any],
    *,
    data: Mapping[str, Any],
    probe: Mapping[str, Any],
    checks: list[CheckResult],
) -> None:
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

    if not config.required_provider_ids:
        evidence = _as_dict(provider.get("probeEvidence"))
        probes = evidence.get("probes")
        probes = probes if isinstance(probes, list) else []
        no_provider_probe_attempts = not probes and not evidence.get("required_provider_ids")
        _check(
            checks,
            _disabled_provider_runtime_confirmed(config, provider)
            and no_provider_probe_attempts,
            "runtime:provider_probe",
            (
                "no provider probe is run while external providers are disabled"
                if _disabled_provider_runtime_confirmed(config, provider)
                and no_provider_probe_attempts
                else (
                    f"mode={provider.get('mode')} "
                    f"configurationValid={provider.get('configurationValid')} "
                    f"requiredProviderIds={evidence.get('required_provider_ids')} "
                    f"probeCount={len(probes)}"
                )
            ),
            "provider",
        )
        return

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


def _check_model_registry_refused(
    response: HttpResponse,
    *,
    checks: list[CheckResult],
    registry_bound: bool = False,
) -> None:
    """With production bindings unverified, the model registry must refuse.

    The Learning Hub serves registry reads only through the production MLflow
    binding. When that binding is absent the route answers 503 with the
    composition error; a 200 here would mean model versions (and possibly
    aliases or lineage) were served from somewhere other than the approved
    registry, which is exactly the manufactured state this scope forbids.

    When the runtime reports the remote registry as bound
    (``learninghubRegistryBound``) the reads do come from the approved
    registry, so an empty listing is the honest answer for "no verified
    versions yet". Only an empty listing is accepted there: any served version
    still blocks, because nothing has been verified for production.
    """

    refused = (not response.failed) and response.status == 503
    expected = "503 while production bindings are unverified"
    if registry_bound and not refused:
        payload = _as_dict(response.payload)
        refused = (
            (not response.failed)
            and response.status == 200
            and payload.get("items") == []
            and payload.get("count") == 0
        )
        expected = "503, or 200 with no model versions from the bound registry"
    _check(
        checks,
        refused,
        "models:registry_refused",
        _failure_detail(response, expected=expected),
        _dependency_for(response, "mlflow"),
    )
    markers = find_surrogate_values(response.payload)
    _check(
        checks,
        not markers,
        "models:no_surrogate_markers",
        "none" if not markers else f"paths={markers[:10]}",
        "data-binding",
    )


def _web_origin(web_url: str) -> str:
    parsed = urllib.parse.urlsplit(web_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _cookie_header(cookies: Mapping[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in sorted(cookies.items()) if value)


def _canonical_uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _foreign_tenant_for(own_tenant: str) -> str:
    """A valid tenant UUID guaranteed to differ from the admin's own tenant."""
    if own_tenant != FOREIGN_TENANT_PROBE_ID:
        return FOREIGN_TENANT_PROBE_ID
    return str(UUID(int=UUID(own_tenant).int ^ 1))


def _identity_snapshot(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The identity facts a refused tenant move must leave untouched.

    Returns ``None`` when any of them is absent, so a readback that drops a
    field is treated as changed rather than silently compared as missing.
    """
    subject_id = record.get("subject_id")
    username = record.get("username")
    roles = record.get("roles")
    status = record.get("status")
    scope = record.get("scope")
    if not (
        isinstance(subject_id, str)
        and subject_id
        and isinstance(username, str)
        and username
        and isinstance(roles, list)
        and roles
        and isinstance(status, str)
        and status
        and isinstance(scope, dict)
        and _canonical_uuid(scope.get("tenant_id")) is not None
    ):
        return None
    return {
        "subject_id": subject_id,
        "username": username,
        "roles": sorted(str(r) for r in roles),
        "status": status,
        "scope": json.loads(json.dumps(scope, sort_keys=True)),
    }


def _invitation_provenance(
    record: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
) -> dict[str, str] | None:
    """Bind a pure admin to one real issue/accept pair, never a fake bootstrap.

    Inputs come only from the authenticated identity administration readback.
    No receipt/config switch can waive the existing role or release checks.
    """
    account = record.get("subject_id")
    snapshot = _identity_snapshot(record)
    if snapshot is None or _canonical_uuid(account) != account:
        return None
    tenant = snapshot["scope"]["tenant_id"]
    fixed_scope = {"tenant_id": tenant, "clearance": "CONFIDENTIAL", **{
        axis: [] for axis in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids",
                             "heat_zone_ids", "modules")
    }}
    if snapshot["roles"] != ["platform_admin"] or snapshot["status"] != "active" or snapshot["scope"] != fixed_scope:
        return None
    accepts = [e for e in events if e.get("event_type") == "identity.account.accept"
               and (e.get("actor") == account or _as_dict(e.get("metadata")).get("account_id") == account)]
    if len(accepts) != 1 or any(
        e.get("event_type") == BOOTSTRAP_AUDIT_EVENT
        and _as_dict(e.get("metadata")).get("account_id") == account for e in events
    ):
        return None
    accept = accepts[0]
    meta = _as_dict(accept.get("metadata"))
    invitation = meta.get("invitation_id")
    if _canonical_uuid(invitation) != invitation or invitation is None:
        return None
    issues = [e for e in events if e.get("event_type") == "identity.account.invite"
              and _as_dict(e.get("metadata")).get("invitation_id") == invitation]
    # A duplicate/revoked/replayed lifecycle is not provenance for this account.
    related_accepts = [e for e in events if e.get("event_type") == "identity.account.accept"
                       and _as_dict(e.get("metadata")).get("invitation_id") == invitation]
    if len(issues) != 1 or len(related_accepts) != 1 or any(
        e.get("event_type") == "identity.account.invitation_revoked"
        and _as_dict(e.get("metadata")).get("invitation_id") == invitation for e in events
    ):
        return None
    issue = issues[0]
    preset = _as_dict(issue.get("metadata"))
    actor = issue.get("actor")
    if actor is None or _canonical_uuid(actor) != actor or actor == account or record.get("updated_by") != actor:
        return None
    if (accept.get("actor") != account or meta.get("account_id") != account
            or meta.get("subject_id") != account or meta.get("tenant_id") != tenant
            or meta.get("roles") != ["platform_admin"] or meta.get("scope") != fixed_scope
            or meta.get("status") != "active" or meta.get("must_change") is not False
            or preset.get("tenant_id") != tenant or preset.get("preset_roles") != ["platform_admin"]
            or preset.get("preset_scope") != fixed_scope):
        return None
    for event in (issue, accept):
        if (event.get("outcome") != "success"
                or event.get("resource") != f"identity.invitation:{invitation}"
                or event.get("correlation_id") != f"identity-invitation-{invitation}"
                or _canonical_uuid(event.get("event_id")) != event.get("event_id")
                or event.get("event_id") is None):
            return None
    if issue["event_id"] == accept["event_id"]:
        return None
    try:
        issued = datetime.fromisoformat(issue["timestamp"])
        accepted = datetime.fromisoformat(accept["timestamp"])
        expires = datetime.fromisoformat(preset["expires_at"])
        if (any(t.tzinfo is None for t in (issued, accepted, expires))
                or not issued <= accepted < expires or not timedelta(0) < expires - issued <= timedelta(hours=72)):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return {"invitation_id": invitation, "issue_event_id": issue["event_id"],
            "accept_event_id": accept["event_id"], "issuer_account_id": actor}


def _read_admin_roles(roles: Any) -> bool:
    """Accept only explicit finite read grants, pinned against canonical RBAC.

    Auditor's existing audit export is retained; no new export or mutating
    business privilege is admitted. Future RBAC expansion fails closed here.
    """
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        return False
    if len(set(roles)) != len(roles) or "platform_admin" not in roles:
        return False
    expected = {
        "platform_admin": {(r, a) for r in ("user", "role", "feature_flag")
                           for a in ("view", "create", "update", "delete")},
        "auditor": {(r, "view") for r in ("operator_console", "avm", "listing", "audit",
                                          "model", "decision", "data_quality")} | {("audit", "export")},
        "operator_viewer": {(r, "view") for r in ("operator_console", "operator_network")},
    }
    if not set(roles) <= expected.keys() or not set(roles) & {"auditor", "operator_viewer"}:
        return False
    # The gate also runs as a standalone script, outside a package invocation.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from shared.auth.identity import Role
    from shared.auth.rbac import ROLE_PERMISSIONS

    return all(
        {(p.resource, p.action.value) for p in ROLE_PERMISSIONS.get(Role(role), ())} == expected[role]
        for role in roles
    )


def _payload_mentions(response: HttpResponse, code: str) -> bool:
    return code in json.dumps(response.payload, sort_keys=True)


def _check_dev_admin_session(
    *,
    web: HttpClient | None,
    config: GateConfig,
    correlation_id: str,
    checks: list[CheckResult],
    report: dict[str, Any],
) -> None:
    """Drive the real password sign-in and administration journey through the deployed Web.

    Every step goes through the Web origin exactly as a browser would: the
    password form endpoint, the sealed session cookie, the BFF proxy that
    swaps the cookie for the server-side session bearer, must_change enforcement,
    first-login password rotation if pending, authoritative user administration,
    business shell refusal (for pure platform_admin), and sign-out. No
    bearer, role, or tenant header is injected; the only identity the API sees
    is the one the Web session carries.

    The supported operations exercised, in order:

    1. anonymous session/API reads are refused;
    2. a wrong password is refused without issuing a session;
    3. password sign-in (or initial secret sign-in + first-password rotation if fresh);
    4. the session resolves to that account;
    5. GET /api/v1/operator/users is served from the identity schema, lists the
       active pure admin or explicitly read-enabled admin; the latter must bind
       to /auth/principal and an audited finite canonical read-role grant;
    6. the user audit trail carries the identity.account.bootstrap event;
    7. moving the account itself to a valid foreign tenant UUID is refused by the
       identity tenant policy (422) and the readback account, tenant, full scope,
       roles and status are unchanged;
    8. pure-admin business reads are refused; explicit read-enabled admins must
       successfully read scoped live data and be refused business mutations;
    9. an Operator Console role outside grants is refused (wrong-role probe);
    10. GET /operator?view=admin is served as an authenticated Web page;
    11. sign-out succeeds and the revoked cookie is refused by Web and API.
    """

    operations: list[str] = []
    report["dev_admin"] = {"correlation_id": correlation_id, "operations": operations}
    if web is None:
        _check(
            checks,
            False,
            "session:web_client",
            f"no usable web origin for {config.web_url or '<missing>'}",
            "config",
        )
        return

    origin = _web_origin(config.web_url)
    base = {"accept": "application/json", "x-correlation-id": correlation_id}
    with_origin = {**base, "origin": origin}

    def session_headers(cookies: Mapping[str, str], **extra: str) -> dict[str, str]:
        return {**base, "cookie": _cookie_header(cookies), **extra}

    username = config.dev_admin_username or config.bootstrap_admin_username
    password = config.dev_admin_password or config.bootstrap_admin_password
    initial_password = config.dev_admin_initial_password

    # 1. Anonymous reads are refused by both the Web and the API behind it.
    anonymous_session = web.request("GET", "/auth/session", authenticated=False, headers=base)
    _check(
        checks,
        (not anonymous_session.failed) and anonymous_session.status == 401,
        "session:anonymous_session_denied",
        _failure_detail(anonymous_session, expected="401"),
        "session",
    )
    anonymous_api = web.request(
        "GET", "/api/v1/operator/bootstrap", authenticated=False, headers=base
    )
    _check(
        checks,
        (not anonymous_api.failed) and anonymous_api.status in DENIED_STATUSES,
        "session:anonymous_api_denied",
        _failure_detail(anonymous_api, expected="401/403"),
        "auth",
    )
    operations.append("anonymous_denied")

    # 2. A wrong password is refused and no session cookie is issued.
    invalid = web.request(
        "POST",
        "/login",
        authenticated=False,
        body={
            "username": username,
            "password": f"{password}-live-gate-invalid",
            "returnTo": "/operator?view=admin",
        },
        headers=with_origin,
        follow_redirects=False,
    )
    _check(
        checks,
        (not invalid.failed)
        and invalid.status == 401
        and _payload_mentions(invalid, "AUTH_INVALID_CREDENTIALS")
        and not any(invalid.cookies.values()),
        "session:invalid_credentials_refused",
        (
            f"{_failure_detail(invalid, expected='401 AUTH_INVALID_CREDENTIALS')} "
            f"sessionIssued={any(invalid.cookies.values())}"
        ),
        "session",
    )
    operations.append("invalid_password_refused")

    # 3. Password sign-in / First-login rotation handling:
    login = web.request(
        "POST",
        "/login",
        authenticated=False,
        body={
            "username": username,
            "password": password,
            "returnTo": "/operator?view=admin",
        },
        headers=with_origin,
        follow_redirects=False,
    )
    cookies = {name: value for name, value in login.cookies.items() if value}
    signed_in = (
        (not login.failed)
        and login.status == 200
        and login.payload.get("ok") is True
        and login.payload.get("subject") == username
        and bool(cookies)
    )

    if signed_in:
        users_probe = web.request(
            "GET", "/api/v1/operator/users", authenticated=False, headers=session_headers(cookies)
        )
        if not users_probe.failed and users_probe.status == 403 and _payload_mentions(users_probe, "PASSWORD_CHANGE_REQUIRED"):
            _check(
                checks,
                False,
                "session:must_change_enforced",
                "status=403 PASSWORD_CHANGE_REQUIRED: first-login rotation requires distinct initial secret and operational password; equal-input pseudo-rotation refused",
                "auth",
            )
            return
        _check(
            checks,
            signed_in,
            "session:password_login",
            f"{_failure_detail(login, expected='200 ok')} sessionCookie={'issued' if cookies else 'missing'}",
            "session",
        )
        if not signed_in:
            return
        operations.append("password_login")
    elif (not login.failed) and login.status == 401 and initial_password:
        if initial_password == password:
            _check(
                checks,
                False,
                "session:password_login",
                "initial bootstrap secret equals operational password; distinct credentials required for first-login rotation",
                "session",
            )
            return
        init_login = web.request(
            "POST",
            "/login",
            authenticated=False,
            body={
                "username": username,
                "password": initial_password,
                "returnTo": "/operator?view=admin",
            },
            headers=with_origin,
            follow_redirects=False,
        )
        init_cookies = {name: value for name, value in init_login.cookies.items() if value}
        init_signed_in = (
            (not init_login.failed)
            and init_login.status == 200
            and init_login.payload.get("ok") is True
            and init_login.payload.get("subject") == username
            and bool(init_cookies)
        )
        if not init_signed_in:
            _check(
                checks,
                False,
                "session:password_login",
                _failure_detail(init_login, expected="200 ok for initial secret"),
                "session",
            )
            return
        users_probe = web.request(
            "GET", "/api/v1/operator/users", authenticated=False, headers=session_headers(init_cookies)
        )
        must_change_refused = (not users_probe.failed) and users_probe.status == 403 and _payload_mentions(users_probe, "PASSWORD_CHANGE_REQUIRED")
        _check(
            checks,
            must_change_refused,
            "session:must_change_enforced",
            _failure_detail(users_probe, expected="403 PASSWORD_CHANGE_REQUIRED"),
            "auth",
        )
        if not must_change_refused:
            return
        rotate_resp = web.request(
            "POST",
            "/auth/password",
            authenticated=False,
            body={
                "currentPassword": initial_password,
                "newPassword": password,
                "confirmPassword": password,
            },
            headers=session_headers(init_cookies, origin=origin),
            follow_redirects=False,
        )
        rotated = (not rotate_resp.failed) and rotate_resp.status == 200 and rotate_resp.payload.get("ok") is True
        _check(
            checks,
            rotated,
            "session:first_login_password_rotated",
            _failure_detail(rotate_resp, expected="200 ok (password rotated)"),
            "auth",
        )
        if not rotated:
            return
        login = web.request(
            "POST",
            "/login",
            authenticated=False,
            body={
                "username": username,
                "password": password,
                "returnTo": "/operator?view=admin",
            },
            headers=with_origin,
            follow_redirects=False,
        )
        cookies = {name: value for name, value in login.cookies.items() if value}
        signed_in = (
            (not login.failed)
            and login.status == 200
            and login.payload.get("ok") is True
            and login.payload.get("subject") == username
            and bool(cookies)
        )
        _check(
            checks,
            signed_in,
            "session:password_login",
            f"{_failure_detail(login, expected='200 ok')} sessionCookie={'issued' if cookies else 'missing'}",
            "session",
        )
        if not signed_in:
            return
        operations.append("password_rotated_and_logged_in")
    else:
        _check(
            checks,
            False,
            "session:password_login",
            f"{_failure_detail(login, expected='200 ok')} sessionCookie={'issued' if cookies else 'missing'}",
            "session",
        )
        return

    # 4. The session resolves to the signed-in account.
    current = web.request(
        "GET", "/auth/session", authenticated=False, headers=session_headers(cookies)
    )
    _check(
        checks,
        (not current.failed)
        and current.status == 200
        and current.payload.get("subject") == username,
        "session:session_resolves_account",
        (
            f"status={current.status} subjectMatches="
            f"{current.payload.get('subject') == username}"
            if not current.failed
            else current.error
        ),
        "session",
    )
    operations.append("session_read")

    # 5. Authoritative user list from identity schema.
    users = web.request(
        "GET", "/api/v1/operator/users", authenticated=False, headers=session_headers(cookies)
    )
    listed = users.payload.get("users") if not users.failed else None
    listed = [u for u in listed if isinstance(u, dict)] if isinstance(listed, list) else []
    own = [
        u
        for u in listed
        if u.get("username") == username
        or _as_dict(u.get("attributes")).get("username") == username
    ]
    own_record = own[0] if len(own) == 1 else {}
    read_admin = (
        _read_admin_roles(own_record.get("roles"))
        and own_record.get("status") == "active"
        and _as_dict(own_record.get("attributes")).get("identity_source") == "identity.accounts"
    )
    pure_admin = (
        sorted(str(r) for r in own_record.get("roles") or []) == ["platform_admin"]
        and own_record.get("status") == "active"
        and _as_dict(own_record.get("attributes")).get("identity_source") == "identity.accounts"
    )
    _check(
        checks,
        (not users.failed) and users.status == 200 and (pure_admin or read_admin),
        "admin:identity_user_list",
        (
            (
                f"{_failure_detail(users, expected='200')}"
                + (
                    " (first-login password rotation is still pending)"
                    if _payload_mentions(users, "PASSWORD_CHANGE_REQUIRED")
                    else ""
                )
            )
            if users.failed or users.status != 200
            else (
                f"status=200 users={len(listed)} selfListed={bool(own)} "
                f"roles={own_record.get('roles')} "
                f"identitySource={_as_dict(own_record.get('attributes')).get('identity_source')}"
            )
        ),
        _dependency_for(users, "auth"),
    )
    operations.append("identity_user_list")
    report["dev_admin"]["account_mode"] = "read-enabled-admin" if read_admin else "pure-admin"
    if users.failed or users.status != 200 or not (pure_admin or read_admin):
        return
    if config.dev_admin_bundle_account_id:
        matched = (pure_admin and own_record.get("subject_id") == config.dev_admin_bundle_account_id
                   and _as_dict(own_record.get("scope")).get("tenant_id") == config.dev_admin_bundle_tenant_id)
        _check(checks, matched, "admin:credential_bundle_account_bound",
               f"authoritativeMatchedPairAccount={matched}", "auth")
        if not matched:
            return
    if read_admin:
        principal = web.request(
            "GET", "/api/v1/auth/principal", authenticated=False, headers=session_headers(cookies)
        )
        snapshot = _identity_snapshot(own_record)
        bound = (
            not principal.failed and principal.status == 200 and snapshot is not None
            and _canonical_uuid(snapshot["subject_id"]) is not None
            and principal.payload.get("account_id") == snapshot["subject_id"]
            and principal.payload.get("tenant_id") == snapshot["scope"]["tenant_id"]
            and _read_admin_roles(principal.payload.get("roles"))
            and sorted(principal.payload["roles"]) == snapshot["roles"]
            and not current.failed and current.status == 200
            and current.payload.get("subject") == snapshot["username"]
        )
        _check(checks, bound, "admin:read_principal_bound",
               f"status={principal.status} accountTenantRolesBound={bound}", "auth")
        if not bound:
            return
        report["dev_admin"]["read_account_binding"] = {
            "account_id": snapshot["subject_id"],
            "tenant_id": snapshot["scope"]["tenant_id"],
            "roles": snapshot["roles"],
            "canonical_finite_grants_verified": True,
        }

    # 6. Genuine bootstrap OR a strict invitation issue/accept lifecycle.
    trail = web.request(
        "GET",
        "/api/v1/operator/users/audit-trail",
        authenticated=False,
        headers=session_headers(cookies),
    )
    events = trail.payload.get("events") if not trail.failed else None
    events = [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []
    if config.dev_admin_bundle_account_id:
        from delivery_toolchain.release.provision_dev_smoke import DevCredentialBundle, credential_bundle_acknowledged
        acknowledged = credential_bundle_acknowledged(DevCredentialBundle(
            username, "", config.dev_admin_bundle_account_id, config.dev_admin_bundle_tenant_id,
            config.dev_admin_bundle_execution_id,
        ), events)
        _check(checks, acknowledged, "admin:credential_bundle_durable_acknowledgement",
               f"reservedIntentAcknowledgementWithoutQuarantine={acknowledged}", "audit")
        if not acknowledged:
            return
    bootstrap_events = [
        e
        for e in events
        if e.get("event_type") == BOOTSTRAP_AUDIT_EVENT
        and own_record
        and _as_dict(e.get("metadata")).get("account_id") == own_record.get("subject_id")
    ]
    invited = any(
        e.get("event_type") == "identity.account.accept"
        and (e.get("actor") == own_record.get("subject_id")
             or _as_dict(e.get("metadata")).get("account_id") == own_record.get("subject_id"))
        for e in events
    )
    if config.dev_admin_bundle_account_id and not invited:
        _check(checks, False, "admin:credential_bundle_invitation_required",
               "matched bundle requires genuine invitation provenance", "audit")
        return
    if invited:
        provenance = _invitation_provenance(own_record, events)
        audited = not trail.failed and trail.status == 200 and provenance is not None
        _check(checks, audited, "admin:invitation_audited",
               f"status={trail.status} uniqueTenantBoundIssueAccept={audited}", "audit")
        operations.append("invitation_audit_readback")
        if not audited:
            return
        # Even a pure invited administrator must bind the cookie's subject to
        # the API's server-verified account, tenant and exact finite role set.
        principal = web.request(
            "GET", "/api/v1/auth/principal", authenticated=False, headers=session_headers(cookies)
        )
        bound = (
            not principal.failed and principal.status == 200
            and principal.payload.get("account_id") == own_record["subject_id"]
            and principal.payload.get("tenant_id") == own_record["scope"]["tenant_id"]
            and principal.payload.get("roles") == ["platform_admin"]
            and not current.failed and current.status == 200 and current.payload.get("subject") == username
        )
        _check(checks, bound, "admin:invitation_principal_bound",
               f"status={principal.status} accountTenantRolesBound={bound}", "auth")
        if not bound:
            return
        report["dev_admin"]["identity_provenance"] = {"mode": "invitation", **provenance}
    else:
        _check(
            checks,
            (not trail.failed) and trail.status == 200 and bool(bootstrap_events),
            "admin:bootstrap_audited",
            (
                _failure_detail(trail, expected="200")
                if trail.failed or trail.status != 200
                else f"status=200 identityEvents={len(events)} bootstrapEvents={len(bootstrap_events)}"
            ),
            "audit",
        )
        operations.append("bootstrap_audit_readback")
    if read_admin:
        grant_events = [
            e for e in events
            if e.get("event_type") == "identity.account.roles_updated"
            and _as_dict(e.get("metadata")).get("account_id") == own_record["subject_id"]
            and _as_dict(e.get("metadata")).get("roles_after") == sorted(own_record["roles"])
            and _as_dict(e.get("metadata")).get("scope_after") == own_record["scope"]
            and _as_dict(e.get("metadata")).get("tenant_id") == own_record["scope"]["tenant_id"]
            and _as_dict(e.get("metadata")).get("status") == "active"
        ]
        audited = not trail.failed and trail.status == 200 and bool(grant_events)
        _check(checks, audited, "admin:read_grant_audited",
               f"status={trail.status} matchingExplicitGrants={len(grant_events)}", "audit")
        if not audited:
            return

    # 7. Tenant isolation: moving the admin's own account to a valid foreign
    #    tenant is refused by the identity tenant policy, and the account reads
    #    back exactly as it was. The request is otherwise identical to the
    #    current record, so only the tenant can be the reason for refusal.
    own_snapshot = _identity_snapshot(own_record)
    own_tenant = _canonical_uuid(_as_dict(own_record.get("scope")).get("tenant_id"))
    if own_snapshot is None or own_tenant is None:
        _check(
            checks,
            False,
            "session:cross_tenant_denied",
            "own account record lacks subject/username/roles/status or a UUID scope.tenant_id; "
            "tenant isolation cannot be probed against an unknown tenant",
            "tenant-isolation",
        )
        return
    foreign_tenant = _foreign_tenant_for(own_tenant)
    probe_scope = dict(own_snapshot["scope"])
    probe_scope["tenant_id"] = foreign_tenant
    foreign_probe = web.request(
        "POST",
        "/api/v1/operator/users",
        authenticated=False,
        body={
            "subjectId": own_snapshot["subject_id"],
            "roles": list(own_snapshot["roles"]),
            "scope": probe_scope,
            "status": own_snapshot["status"],
            "reason": "foreign tenant scope boundary probe",
        },
        headers=session_headers(cookies, origin=origin),
        follow_redirects=False,
    )
    probe_detail = _as_dict(foreign_probe.payload).get("detail") if not foreign_probe.failed else None
    foreign_refused = (
        (not foreign_probe.failed)
        and foreign_probe.status == 422
        and isinstance(probe_detail, str)
        and TENANT_POLICY_REFUSAL_TEXT in probe_detail
        and foreign_tenant in probe_detail
    )
    _check(
        checks,
        foreign_refused,
        "session:cross_tenant_denied",
        _failure_detail(
            foreign_probe,
            expected=f"422 identity tenant-policy refusal ('{TENANT_POLICY_REFUSAL_TEXT}')",
        ),
        "tenant-isolation",
    )
    if not foreign_refused:
        return

    readback = web.request(
        "GET", "/api/v1/operator/users", authenticated=False, headers=session_headers(cookies)
    )
    readback_list = readback.payload.get("users") if not readback.failed else None
    readback_list = [u for u in readback_list if isinstance(u, dict)] if isinstance(readback_list, list) else []
    readback_own = [
        u for u in readback_list if u.get("subject_id") == own_snapshot["subject_id"]
    ]
    readback_snapshot = _identity_snapshot(readback_own[0]) if len(readback_own) == 1 else None
    tenant_unmodified = (
        (not readback.failed)
        and readback.status == 200
        and readback_snapshot is not None
        and readback_snapshot == own_snapshot
    )
    changed = (
        sorted(
            key
            for key in own_snapshot
            if readback_snapshot is None or readback_snapshot.get(key) != own_snapshot[key]
        )
        if not readback.failed
        else []
    )
    _check(
        checks,
        tenant_unmodified,
        "admin:foreign_tenant_unmodified_readback",
        (
            f"status={readback.status} matches={len(readback_own)} "
            f"changedOrMissing={changed or 'none'}"
            if not readback.failed
            else readback.error
        ),
        "tenant-isolation",
    )
    if not tenant_unmodified:
        return
    operations.append("cross_tenant_denied")

    # 8. Business shell is denied (pure platform_admin has no business read).
    business = web.request(
        "GET", "/api/v1/operator/bootstrap", authenticated=False, headers=session_headers(cookies)
    )
    if read_admin:
        markers = find_surrogate_values(business.payload)
        served = (not business.failed and business.status == 200
                  and _declared_data_mode(business.payload) == "live"
                  and bool(_operator_source(business.payload)) and not markers)
        _check(checks, served, "admin:scoped_business_read",
               f"status={business.status} liveOriginVerified={served}", "data-binding")
        detail = web.request(
            "GET", f"/api/v1/operator/users/{own_snapshot['subject_id']}",
            authenticated=False, headers=session_headers(cookies),
        )
        _check(checks, not detail.failed and detail.status == 200
               and _identity_snapshot(detail.payload) == own_snapshot,
               "admin:scoped_account_read", f"status={detail.status}", "tenant-isolation")
        # Each probe deliberately violates a typed DTO field, so validation
        # stops before the handler even if its authorization guard regresses.
        # In particular, {} is VALID for batch scoring and can score all sites.
        # A non-existent id alone is not a safety boundary. Only 403 passes;
        # validation 422 must block the gate, never count as authorization proof.
        for label, path, invalid_body in (
            ("write", "network-listings/intake/submit", {"url": {}}),
            ("approve", "network-reviews/live-gate-no-such-review/decide", {"decision": {}}),
            ("execute", "network-scoring/score", {"candidateIds": {}}),
            ("publish", "governance/decisions", {"approvalId": {}, "action": {}}),
        ):
            refused = web.request(
                "POST", f"/api/v1/operator/{path}", body=invalid_body, authenticated=False,
                headers=session_headers(cookies, origin=origin), follow_redirects=False,
            )
            _check(checks, not refused.failed and refused.status == 403,
                   f"admin:business_{label}_denied", _failure_detail(refused, expected="403"), "auth")
        operations.append("scoped_read_and_business_mutations_denied")
    else:
        _check(
            checks,
            (not business.failed) and business.status == 403,
            "admin:business_shell_denied",
            _failure_detail(business, expected="403 (platform_admin holds no business read)"),
            "auth",
        )
        operations.append("business_shell_denied")

    # 9. RBAC wrong-role probe:
    denied_role = config.dev_admin_denied_role or "cs-lead"
    wrong_role = web.request(
        "GET",
        "/api/v1/operator/bootstrap",
        authenticated=False,
        headers=session_headers(cookies, **{"x-operator-role": denied_role}),
    )
    _check(
        checks,
        (not wrong_role.failed) and wrong_role.status == 403,
        "session:wrong_role_denied",
        _failure_detail(wrong_role, expected=f"403 for role {denied_role}"),
        "auth",
    )
    operations.append("wrong_role_denied")

    # 10. Admin view served to the session.
    page = web.request(
        "GET",
        "/operator?view=admin",
        authenticated=False,
        headers={**session_headers(cookies), "accept": "text/html"},
        follow_redirects=False,
    )
    _check(
        checks,
        (not page.failed) and page.status == 200,
        "admin:admin_page_served",
        _failure_detail(page, expected="200 (not a /login redirect)"),
        "session",
    )
    operations.append("admin_page")

    # 11. Logout and revocation.
    logout = web.request(
        "POST",
        "/auth/logout",
        authenticated=False,
        headers=session_headers(cookies, origin=origin),
        follow_redirects=False,
    )
    cleared = all(not logout.cookies.get(name, "x") for name in cookies)
    _check(
        checks,
        (not logout.failed)
        and logout.status == 200
        and logout.payload.get("ok") is True
        and cleared,
        "session:logout",
        f"{_failure_detail(logout, expected='200 ok')} cookieCleared={cleared}",
        "session",
    )
    replay_session = web.request(
        "GET", "/auth/session", authenticated=False, headers=session_headers(cookies)
    )
    _check(
        checks,
        (not replay_session.failed) and replay_session.status == 401,
        "session:revoked_session_refused",
        _failure_detail(replay_session, expected="401 after logout"),
        "session",
    )
    replay_api = web.request(
        "GET", "/api/v1/operator/users", authenticated=False, headers=session_headers(cookies)
    )
    _check(
        checks,
        (not replay_api.failed) and replay_api.status in DENIED_STATUSES,
        "admin:logout_revokes_admin_api",
        f"replay={_failure_detail(replay_api, expected='401/403')}",
        "session",
    )
    operations.append("logout_and_revocation")

    # The JSON contract above does not prove browser form redirects. Exercise
    # URL-encoded submissions separately, without following any redirect or
    # retaining a second live session after this journey.
    form_headers = {
        "accept": "text/html",
        "content-type": "application/x-www-form-urlencoded",
        "origin": origin,
        "x-correlation-id": correlation_id,
    }
    return_to = "/operator?view=admin"
    form_invalid = web.request(
        "POST", "/login", authenticated=False,
        body={"username": f"live-gate-form-{correlation_id}",
              "password": "invalid-form-diagnostic", "returnTo": return_to},
        headers=form_headers, follow_redirects=False,
    )
    failure_location = urllib.parse.urlsplit(form_invalid.location)
    failure_query = urllib.parse.parse_qs(failure_location.query)
    _check(
        checks,
        (not form_invalid.failed) and form_invalid.status == 303
        and _origin(form_invalid.location) == _origin(origin)
        and failure_location.path == "/login"
        and failure_query == {"error": ["AUTH_INVALID_CREDENTIALS"], "returnTo": [return_to]}
        and not any(form_invalid.cookies.values()),
        "session:html_form_invalid_redirect",
        f"status={form_invalid.status} sameOrigin={_origin(form_invalid.location) == _origin(origin)}",
        "session",
    )
    form_login = web.request(
        "POST", "/login", authenticated=False,
        body={"username": username, "password": password, "returnTo": return_to},
        headers=form_headers, follow_redirects=False,
    )
    form_cookies = {name: value for name, value in form_login.cookies.items() if value}
    destination = urllib.parse.urlsplit(form_login.location)
    form_signed_in = (
        (not form_login.failed) and form_login.status == 303 and bool(form_cookies)
        and _origin(form_login.location) == _origin(origin)
        and destination.path == "/operator" and destination.query == "view=admin"
        and not destination.fragment
    )
    _check(
        checks, form_signed_in, "session:html_form_password_login",
        f"status={form_login.status} sameOrigin={_origin(form_login.location) == _origin(origin)} sessionCookie={'issued' if form_cookies else 'missing'}",
        "session",
    )
    if not form_cookies:
        return
    try:
        form_session = web.request(
            "GET", "/auth/session", authenticated=False, headers=session_headers(form_cookies)
        )
        _check(
            checks, (not form_session.failed) and form_session.status == 200
            and form_session.payload.get("subject") == username,
            "session:html_form_session_resolves_account",
            f"status={form_session.status} subjectMatches={form_session.payload.get('subject') == username}",
            "session",
        )
        form_page = web.request(
            "GET", return_to, authenticated=False,
            headers={**session_headers(form_cookies), "accept": "text/html"},
            follow_redirects=False,
        )
        _check(
            checks, (not form_page.failed) and form_page.status == 200,
            "admin:html_form_admin_page_served",
            _failure_detail(form_page, expected="200 (not a /login redirect)"), "session",
        )
    finally:
        form_logout = web.request(
            "POST", "/auth/logout", authenticated=False,
            headers=session_headers(form_cookies, origin=origin), follow_redirects=False,
        )
        _check(
            checks, (not form_logout.failed) and form_logout.status == 200
            and form_logout.payload.get("ok") is True
            and all(not form_logout.cookies.get(name, "x") for name in form_cookies),
            "session:html_form_logout", _failure_detail(form_logout, expected="200 ok"), "session",
        )
    form_replay = web.request(
        "GET", "/auth/session", authenticated=False, headers=session_headers(form_cookies)
    )
    _check(
        checks, (not form_replay.failed) and form_replay.status == 401,
        "session:html_form_revoked_session_refused",
        _failure_detail(form_replay, expected="401 after logout"), "session",
    )
    operations.append("html_form_login_and_revocation")


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
    response: HttpResponse,
    *,
    config: GateConfig,
    provider: Mapping[str, Any],
    checks: list[CheckResult],
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

    if not config.required_provider_ids:
        disabled = _disabled_provider_runtime_confirmed(config, provider)
        blocked = [
            item
            for item in items
            if isinstance(item, dict)
            and item.get("provider_id") == config.probe_provider_id
            and str(item.get("status") or "").upper() == "FAILED"
            and str(item.get("data_status") or "").upper() == "BLOCKED"
            and not item.get("raw_snapshot_id")
            and not item.get("canonical_snapshot_id")
            and any(
                isinstance(alert, dict)
                and alert.get("reason_code") == PROVIDER_MODE_DISABLED_REASON_CODE
                for alert in (item.get("alerts") or [])
            )
        ]
        _check(
            checks,
            disabled and bool(blocked),
            "data:ingestion_runs",
            (
                "disabled provider receipt is blocked with no snapshot written"
                if disabled and blocked
                else (
                    f"runtime provider disabled={disabled} "
                    f"blockedReceipts={len(blocked)} items={len(items)}"
                )
            ),
            "external-data",
        )
        # The disabled branch still runs ``data:no_surrogate_markers``: a
        # consumer-only release must prove the ingestion-run history it serves
        # carries no placeholder/surrogate values, exactly like a live one.
        _check_no_surrogate_markers(response, checks=checks)
        return

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

    _check_no_surrogate_markers(response, checks=checks)


def _check_no_surrogate_markers(
    response: HttpResponse, *, checks: list[CheckResult]
) -> None:
    """Assert the served ingestion-run history carries no surrogate values."""

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
    provider: Mapping[str, Any],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    runtime_provider_mode = str(provider.get("mode") or "").strip().lower()
    configured_provider_mode = config.external_provider_mode.strip().lower()
    if runtime_provider_mode != configured_provider_mode:
        _check(
            checks,
            False,
            "worker:provider_mode_alignment",
            (
                f"configured={configured_provider_mode or '<missing>'} "
                f"runtime={runtime_provider_mode or '<missing>'}; "
                "worker probe was not dispatched"
            ),
            "worker",
        )
        report["worker"] = {"skipped": False, "dispatched": False}
        return

    providers_disabled = _disabled_provider_runtime_confirmed(config, provider)
    if not config.required_provider_ids and not providers_disabled:
        _check(
            checks,
            False,
            "worker:provider_mode_alignment",
            "gate requested disabled provider mode but runtime did not confirm it; worker probe was not dispatched",
            "worker",
        )
        report["worker"] = {"skipped": False, "dispatched": False}
        return

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

    terminal_check = "worker:terminal_disabled" if providers_disabled else "worker:terminal_success"
    _check(
        checks,
        status_value == "succeeded",
        terminal_check,
        f"{last_detail} deadline={config.worker_deadline_seconds}s",
        "worker",
    )
    if isinstance(report.get("worker"), dict):
        report["worker"]["terminal_status"] = status_value or None
        report["worker"]["expected_terminal_status"] = "succeeded"
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
    business_journey_receipt: Mapping[str, Any] | None = None,
    business_journey_receipt_note: str = "",
) -> tuple[list[CheckResult], dict[str, Any]]:
    redact = _redactor(
        config.bearer_token,
        config.api_transport_token,
        config.dev_admin_password,
        config.dev_admin_initial_password,
        config.bootstrap_admin_password,
    )
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
            "api_transport_credential_configured": bool(config.api_transport_token),
            "required_provider_ids": list(config.required_provider_ids),
            "snapshot_provider_ids": list(config.snapshot_provider_ids),
            "enrichment_provider_ids": list(config.enrichment_provider_ids),
            "worker_probe_provider_id": config.probe_provider_id,
            "external_provider_mode": config.external_provider_mode,
            "release_profile": config.release_profile,
            "dev_admin_account_configured": bool(
                (config.dev_admin_username or config.bootstrap_admin_username)
                and (config.dev_admin_password or config.bootstrap_admin_password)
            ),
            "dev_admin_initial_password_configured": bool(config.dev_admin_initial_password),
            "bootstrap_admin_account_configured": bool(
                (config.dev_admin_username or config.bootstrap_admin_username)
                and (config.dev_admin_password or config.bootstrap_admin_password)
            ),
            "secret_values_redacted": True,
        },
    }

    if all(check.ok for check in checks):
        _check_release_binding(
            http.request("GET", "/platform/version", authenticated=False),
            config=config,
            checks=checks,
        )
        readiness_response = http.request("GET", "/readiness", authenticated=False)
        _check_runtime_readiness(
            readiness_response,
            config=config,
            checks=checks,
        )
        runtime_details = _as_dict(readiness_response.payload.get("details"))
        runtime_provider = _as_dict(runtime_details.get("provider"))
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
        models_response = http.request("GET", "/api/v1/learninghub/models")
        runtime_models = _as_dict(runtime_details.get("models"))
        if _models_refused_in_scope(config, runtime_models):
            _check_model_registry_refused(
                models_response,
                checks=checks,
                registry_bound=runtime_models.get("learninghubRegistryBound") is True,
            )
        else:
            _check_model_lineage(models_response, checks=checks)
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
            provider=runtime_provider,
            monotonic=monotonic,
            sleep=sleep,
        )
        _check_source_data(
            http.request("GET", "/api/v1/external-data/ingestion-runs?limit=100"),
            config=config,
            provider=runtime_provider,
            checks=checks,
        )
        if config.dev_admin:
            _check_dev_admin_session(
                web=web_http,
                config=config,
                correlation_id=f"{correlation_id}-session",
                checks=checks,
                report=report,
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
    full_acceptance = evaluate_full_acceptance(
        config,
        runtime_ok=report["ok"],
        receipt=business_journey_receipt,
        receipt_note=business_journey_receipt_note,
        redact=redact,
    )
    report["full_acceptance"] = full_acceptance
    # A narrowed scope never stands as model or full-product evidence, even
    # when it passes. Say so in the receipt itself, not only in the docs.
    # Full product acceptance additionally needs the sealed six-journey
    # business receipt (ODP-BUSINESS-LIVE-E2E-COVERAGE-001); a passing
    # runtime gate alone only proves the platform, not the business journeys.
    report["release_profile"] = {
        "name": config.release_profile,
        "model_readiness_claimed": config.release_profile == RELEASE_PROFILE_FULL,
        "full_product_acceptance_claimed": full_acceptance["status"] == "PASSED",
    }
    return checks, report


def evaluate_full_acceptance(
    config: GateConfig,
    *,
    runtime_ok: bool,
    receipt: Mapping[str, Any] | None,
    receipt_note: str = "",
    redact: Callable[[Any], str] = str,
) -> dict[str, Any]:
    """Bind the six-journey business receipt to this exact runtime gate run.

    PASSED only when the sealed profile is full, the runtime gate passed, and
    every receipt requirement (release SHA, manifest digest, sealed profile,
    six PASSED journeys, no secrets) holds. A narrowed profile is
    NOT_ADMITTED; anything else is BLOCKED with the named dependency.
    """

    receipt_checks = business_journeys.verify_receipt(
        receipt,
        expected_sha=config.expected_sha,
        expected_digest=config.expected_manifest_digest,
        release_profile=config.release_profile,
    )
    journey_entries = _as_dict(receipt.get("journeys")) if isinstance(receipt, Mapping) else {}
    journeys = {
        journey_id: str(_as_dict(journey_entries.get(journey_id)).get("status") or "BLOCKED")
        for journey_id in business_journeys.JOURNEY_IDS
    }
    blockers = [
        {
            "check": check.name,
            "dependency": check.dependency,
            "detail": redact(check.detail),
            "next_action": DEPENDENCY_ACTIONS.get(check.dependency, "Investigate the runtime."),
        }
        for check in receipt_checks
        if not check.ok
    ]
    if not runtime_ok:
        blockers.insert(
            0,
            {
                "check": "business_journeys:runtime_gate",
                "dependency": "business-journey",
                "detail": "the runtime gate did not pass; business journeys cannot stand alone",
                "next_action": "Clear the runtime gate blockers first.",
            },
        )
    if config.release_profile != RELEASE_PROFILE_FULL:
        status = "NOT_ADMITTED"
        journeys = {journey_id: "NOT_ADMITTED" for journey_id in journeys}
    elif blockers:
        status = "BLOCKED"
    else:
        status = "PASSED"
    return {
        "status": status,
        "release_profile": config.release_profile,
        "runtime_gate_ok": bool(runtime_ok),
        "receipt_provided": isinstance(receipt, Mapping),
        "receipt_note": redact(receipt_note),
        "receipt_digest": (
            str(receipt.get("receipt_digest") or "") if isinstance(receipt, Mapping) else ""
        ),
        "expected_manifest_digest": config.expected_manifest_digest,
        "journeys": journeys,
        "checks": [
            {**asdict(check), "detail": redact(check.detail)} for check in receipt_checks
        ],
        "blockers": blockers,
        "blocking_dependencies": sorted({blocker["dependency"] for blocker in blockers}),
    }


def load_business_journey_receipt(path: Path | None) -> tuple[dict[str, Any] | None, str]:
    """Read the receipt; an absent or unreadable file is no receipt (fail closed)."""

    if path is None or not str(path).strip():
        return None, "no business journey receipt configured"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"business journey receipt unreadable: {type(exc).__name__}"
    if not isinstance(payload, dict):
        return None, "business journey receipt is not a JSON object"
    return payload, ""


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
        "--release-profile",
        default=os.environ.get(RELEASE_PROFILE_ENV, RELEASE_PROFILE_FULL),
        help=(
            "Acceptance scope of the admitted manifest: 'full' (default) or "
            "'dev-admin' (dev only; adds the Web password-session journey and "
            "requires missing models to stay refused)."
        ),
    )
    parser.add_argument(
        "--allow-http",
        action="store_true",
        help="Permit HTTP only for an explicitly controlled non-production target.",
    )
    parser.add_argument(
        "--business-journey-receipt",
        type=Path,
        default=(
            Path(os.environ[BUSINESS_JOURNEY_RECEIPT_ENV])
            if os.environ.get(BUSINESS_JOURNEY_RECEIPT_ENV, "").strip()
            else None
        ),
        help=(
            "Sealed six-journey receipt from delivery_toolchain/e2e/"
            "live_business_journeys.py; required for full product acceptance."
        ),
    )
    parser.add_argument(
        "--expected-manifest-digest",
        default=os.environ.get(MANIFEST_DIGEST_ENV, ""),
        help="Admitted manifest digest the business journey receipt must be bound to.",
    )
    parser.add_argument(
        "--require-full-acceptance",
        action="store_true",
        help=(
            "Exit non-zero unless full product acceptance is claimed (runtime "
            "gate passed and the six-journey receipt verified)."
        ),
    )
    return parser.parse_args(argv)


def _required_providers(args: argparse.Namespace) -> tuple[str, ...]:
    if os.environ.get("ODP_EXTERNAL_PROVIDER_MODE", "").strip().lower() == "disabled":
        return ()
    if args.required_provider:
        return tuple(sorted({value.strip() for value in args.required_provider if value.strip()}))
    declared = os.environ.get(PRODUCTION_PROVIDER_IDS_ENV, "")
    parsed = tuple(
        sorted({value.strip() for value in re.split(r"[,\s]+", declared) if value.strip()})
    )
    return parsed or DEFAULT_REQUIRED_PROVIDER_IDS


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, read_dev_credential_bundle

    try:
        bundle = read_dev_credential_bundle(
            os.environ.get("ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", ""),
            environment=args.expected_deployment.strip().lower(),
            release_profile=str(args.release_profile or "").strip().lower(),
        )
    except ProvisioningRefused:
        # No raw JSON/password, diagnostics, fallback or network on parse failure.
        print("Live E2E gate refused: PROVISIONING_CREDENTIAL_BUNDLE_INVALID", file=sys.stderr)
        return 2
    config = GateConfig(
        api_url=args.api_url.strip(),
        expected_sha=args.expected_sha.strip().lower(),
        bearer_token=os.environ.get(args.bearer_token_env, "").strip(),
        api_transport_token=os.environ.get(API_TRANSPORT_TOKEN_ENV, "").strip(),
        operator_role=args.operator_role.strip(),
        web_url=args.web_url.strip(),
        expected_deployment=args.expected_deployment.strip().lower(),
        operator_subject=args.operator_subject.strip(),
        operator_tenant=args.operator_tenant.strip(),
        required_provider_ids=_required_providers(args),
        external_provider_mode=os.environ.get("ODP_EXTERNAL_PROVIDER_MODE", "live").strip().lower(),
        worker_probe_provider_id=args.worker_probe_provider.strip(),
        worker_deadline_seconds=args.worker_deadline_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        timeout=args.timeout,
        allow_http=args.allow_http,
        release_profile=str(args.release_profile or "").strip().lower(),
        # The six-journey receipt is bound to this admitted manifest digest;
        # without it full acceptance can never be claimed (fail closed).
        expected_manifest_digest=str(args.expected_manifest_digest or "").strip().lower(),
        dev_admin_username=bundle.username if bundle else (os.environ.get(DEV_ADMIN_USERNAME_ENV, "").strip() or os.environ.get(BOOTSTRAP_ADMIN_USERNAME_ENV, "").strip()),
        # No stripping and NO per-field fallback when a matched bundle is present.
        dev_admin_password=bundle.password if bundle else (os.environ.get(DEV_ADMIN_PASSWORD_ENV, "") or os.environ.get(BOOTSTRAP_ADMIN_PASSWORD_ENV, "")),
        dev_admin_initial_password="" if bundle else (os.environ.get(DEV_ADMIN_INITIAL_PASSWORD_ENV, "") or os.environ.get(BOOTSTRAP_SECRET_ENV, "")),
        dev_admin_bundle_account_id=bundle.account_id if bundle else "",
        dev_admin_bundle_tenant_id=bundle.tenant_id if bundle else "",
        dev_admin_bundle_execution_id=bundle.execution_id if bundle else "",
        dev_admin_denied_role=os.environ.get(DEV_ADMIN_DENIED_ROLE_ENV, "").strip() or "cs-lead",
        bootstrap_admin_username="" if bundle else os.environ.get(BOOTSTRAP_ADMIN_USERNAME_ENV, "").strip(),
        bootstrap_admin_password="" if bundle else os.environ.get(BOOTSTRAP_ADMIN_PASSWORD_ENV, ""),
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
        transport_token=config.api_transport_token,
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

    receipt, receipt_note = load_business_journey_receipt(args.business_journey_receipt)
    _, report = evaluate_gate(
        config,
        http=http,
        worker_driver=worker_driver,
        correlation_id=correlation_id,
        now=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        web_http=_web_client(config, correlation_id),
        business_journey_receipt=receipt,
        business_journey_receipt_note=receipt_note,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if report["ok"]:
        print(f"Live E2E gate passed. report={args.output}")
        return _report_full_acceptance(report, require=args.require_full_acceptance)

    print("Live E2E gate failed. Blocking runtime dependencies:")
    for dependency in report["blocking_dependencies"]:
        print(f"* {dependency}: {DEPENDENCY_ACTIONS.get(dependency, 'Investigate the runtime.')}")
        for blocker in report["blockers"]:
            if blocker["dependency"] == dependency:
                print(f"  - {blocker['check']}: {blocker['detail']}")
    print(f"report={args.output}")
    _report_full_acceptance(report, require=args.require_full_acceptance)
    return 1


def _report_full_acceptance(report: Mapping[str, Any], *, require: bool) -> int:
    """Print the full-acceptance verdict; non-zero only when it is required."""

    full = _as_dict(report.get("full_acceptance"))
    status = full.get("status") or "BLOCKED"
    print(f"Full product acceptance: {status} journeys={full.get('journeys')}")
    for blocker in full.get("blockers") or []:
        print(f"  - {blocker['check']} [{blocker['dependency']}]: {blocker['detail']}")
    claimed = _as_dict(report.get("release_profile")).get("full_product_acceptance_claimed") is True
    if require and not claimed:
        print("Full product acceptance was required and is not claimed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
