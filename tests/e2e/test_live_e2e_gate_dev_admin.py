"""The dev-admin release profile of the live E2E gate (ODP-DEV-ADMIN-RELEASE-READINESS-001).

Starts from the fully passing deployment of ``test_live_e2e_gate`` and changes
exactly what the dev-admin scope is about: production models are absent (and
must stay truthfully refused), and administration is proven by a real Web
password sign-in journey. Every negative breaks one fact and asserts the gate
names it. The full profile is re-run against the same missing-model runtime to
prove it still refuses.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1].parent
_spec = importlib.util.spec_from_file_location(
    "live_e2e_gate_base_fixtures", ROOT / "tests/e2e/test_live_e2e_gate.py"
)
assert _spec and _spec.loader
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)
gate = base.gate

USERNAME = "dev-ops-admin"
PASSWORD = "operator-chosen-passphrase-value"
DENIED_ROLE = "cs-lead"
SESSION_COOKIE = "__Host-oday_web_session"
SESSION_VALUE = "sealed-session-reference"
SESSION_CORRELATION_ID = f"{base.CORRELATION_ID}-session"
SESSION_JOB_ID = "job-session-0001"
MODEL_ERROR = (
    "forecastops: MLFLOW_TRACKING_URI_REQUIRED: production model runtime is not configured"
)


# ---------------------------------------------------------------------------
# Runtime with production models absent
# ---------------------------------------------------------------------------


def missing_model_readiness(profile: str = "dev-admin") -> dict[str, Any]:
    payload = deepcopy(base.disabled_readiness_payload())
    details = payload["details"]
    details["releaseProfile"] = {
        "name": profile,
        "valid": True,
        "modelReadinessClaimed": profile == "full",
        "error": None,
    }
    models = details["models"]
    models.update(
        {
            "mode": "mlflow-production-unverified",
            "productionBindingsReady": False,
            "autoSeeded": False,
            "error": MODEL_ERROR,
            "blockingReasons": ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"],
        }
    )
    models["capabilities"]["forecastops"].update(
        {
            "available": False,
            "reasonCode": "MLFLOW_TRACKING_URI_REQUIRED",
            "error": "production model runtime is not configured",
        }
    )
    return payload


def registry_refused() -> Any:
    return base.response(
        503,
        {
            "error": {
                "code": "LEARNINGHUB_RUNTIME_CONFIGURATION_ERROR",
                "message": "Learning Hub production requires an injected remote MLflow registry",
            }
        },
    )


def api_routes(**overrides: Any) -> dict[str, Any]:
    return base.disabled_routes(
        **{
            "anon GET /readiness": base.response(200, missing_model_readiness()),
            "GET /api/v1/learninghub/models": registry_refused(),
            **overrides,
        }
    )


# ---------------------------------------------------------------------------
# The deployed Web origin, as a browser sees it
# ---------------------------------------------------------------------------


class SessionWeb(base.FakeHttp):
    """FakeHttp that also dispatches on whether the session cookie was sent.

    A route key gains a `` [session]`` suffix when the request carries the
    signed-in cookie, so the fixture can model the server deciding on the
    cookie it was actually given rather than on call order.
    """

    def __init__(self, routes: dict[str, Any]) -> None:
        super().__init__(routes)
        self.headers_seen: list[tuple[str, dict[str, str]]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
        self.headers_seen.append((f"{method} {path}", headers))
        assert kwargs.get("authenticated") is False, "web requests never inject app identity"
        assert "authorization" not in headers and "x-tenant-id" not in headers
        if f"{SESSION_COOKIE}={SESSION_VALUE}" in headers.get("cookie", ""):
            key = f"{method.upper()} {path} [session]"
            if key in self.routes:
                self.calls.append(key)
                route = self.routes[key]
                if callable(route):
                    route = route(kwargs.get("body"), headers)
                return deepcopy(route)
        return super().request(method, path, **kwargs)


def login_route(body: Any, headers: Any) -> Any:
    assert headers.get("origin") == base.WEB_URL, "login must carry the Web origin (CSRF)"
    if body == {"username": USERNAME, "password": PASSWORD, "returnTo": "/operator"}:
        return base.response(
            200,
            {"ok": True, "subject": USERNAME, "returnTo": "/operator"},
            cookies={SESSION_COOKIE: SESSION_VALUE},
        )
    return base.response(
        401,
        {"error": {"code": "AUTH_INVALID_CREDENTIALS", "summary": "Invalid username or password."}},
    )


def session_jobs_route(body: Any, headers: Any) -> Any:
    payload = body.get("payload") or {}
    if payload.get("tenant_id"):
        return base.response(
            403,
            {"error": {"code": "TENANT_SCOPE_MISMATCH", "message": "tenant mismatch"}},
        )
    return base.response(
        202,
        {
            "job_id": SESSION_JOB_ID,
            "status": "queued",
            "created": True,
            "audit_event_id": "evt-session-1",
            "job": {"job_id": SESSION_JOB_ID, "status": "queued"},
        },
    )


def session_audit() -> dict[str, Any]:
    return {
        "events": [
            {
                "event_type": "job.enqueue",
                "outcome": "accepted",
                "job_id": SESSION_JOB_ID,
                "correlation_id": SESSION_CORRELATION_ID,
                "integrity": {"sequence": 41, "event_hash": "a" * 64},
            },
            {
                "event_type": "job.enqueue",
                "outcome": "denied",
                "correlation_id": SESSION_CORRELATION_ID,
                "metadata": {"status_code": 403},
            },
        ]
    }


def operator_bootstrap() -> Any:
    return base.live_routes()["GET /api/v1/operator/bootstrap"]


def web_routes(**overrides: Any) -> dict[str, Any]:
    denied = base.response(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})
    routes: dict[str, Any] = {
        "anon GET /operator": base.response(
            302, location=f"{base.WEB_URL}/login?returnTo=%2Foperator"
        ),
        # Without (or with a revoked) session cookie.
        "anon GET /auth/session": denied,
        "anon GET /api/v1/operator/bootstrap": denied,
        "anon POST /login": login_route,
        # With the signed-in session cookie.
        "GET /auth/session [session]": base.response(200, {"subject": USERNAME, "expiresAt": 1}),
        "GET /api/v1/operator/bootstrap [session]": operator_bootstrap(),
        "POST /api/v1/jobs [session]": session_jobs_route,
        f"GET /api/v1/jobs/{SESSION_JOB_ID} [session]": base.response(
            200, {"job_id": SESSION_JOB_ID, "job_type": "external-fetch", "status": "queued"}
        ),
        f"GET /api/v1/audit/events?correlation_id={SESSION_CORRELATION_ID} [session]": (
            base.response(200, session_audit())
        ),
        "POST /auth/logout [session]": base.response(
            200, {"ok": True}, cookies={SESSION_COOKIE: ""}
        ),
    }
    routes.update(overrides)
    return routes


class RoleAwareWeb(SessionWeb):
    """Refuse the session's bootstrap when a role outside its grants is asked for,
    and refuse every session read once it has been signed out."""

    def __init__(self, routes: dict[str, Any], *, revoke_on_logout: bool = True) -> None:
        super().__init__(routes)
        self.logged_out = False
        self.revoke_on_logout = revoke_on_logout

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
        has_session = f"{SESSION_COOKIE}={SESSION_VALUE}" in headers.get("cookie", "")
        if has_session and self.logged_out and self.revoke_on_logout:
            self.headers_seen.append((f"{method} {path}", headers))
            return base.response(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})
        if (
            has_session
            and path == "/api/v1/operator/bootstrap"
            and headers.get("x-operator-role") == DENIED_ROLE
            and "role-check" not in self.routes
        ):
            self.headers_seen.append((f"{method} {path}", headers))
            return base.response(403, {"error": {"code": "forbidden"}})
        result = super().request(method, path, **kwargs)
        if has_session and method == "POST" and path == "/auth/logout":
            self.logged_out = True
        return result


def dev_admin_config(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "release_profile": "dev-admin",
        "dev_admin_username": USERNAME,
        "dev_admin_password": PASSWORD,
        "dev_admin_denied_role": DENIED_ROLE,
    }
    values.update(overrides)
    return base.disabled_config(**values)


def run_dev_admin(
    *,
    api: dict[str, Any] | None = None,
    web: Any = None,
    cfg: Any = None,
) -> tuple[list[Any], dict[str, Any], Any]:
    web_http = web if web is not None else RoleAwareWeb(web_routes())
    checks, report = base.run_gate(
        api if api is not None else api_routes(),
        cfg=cfg or dev_admin_config(),
        web_http=web_http,
    )
    return checks, report, web_http


def blockers(report: dict[str, Any]) -> dict[str, str]:
    return {b["check"]: b["dependency"] for b in report["blockers"]}


# ---------------------------------------------------------------------------
# Positive: healthy administration, models absent and refused
# ---------------------------------------------------------------------------


def test_dev_admin_passes_with_missing_models_and_a_real_session_journey() -> None:
    checks, report, web = run_dev_admin()

    assert report["ok"] is True, report["blockers"]
    names = {check.name for check in checks}
    assert {
        "runtime:release_profile",
        "runtime:model_limitation_reported",
        "runtime:model_capability:forecastops",
        "models:registry_refused",
        "session:anonymous_session_denied",
        "session:anonymous_api_denied",
        "session:invalid_credentials_refused",
        "session:password_login",
        "session:session_resolves_account",
        "session:operator_bootstrap",
        "session:wrong_role_denied",
        "session:job_enqueue",
        "session:job_readback",
        "session:cross_tenant_denied",
        "session:audit_persisted",
        "session:logout",
        "session:revoked_session_refused",
        "session:revoked_api_refused",
    } <= names
    # The full-profile model assertions are replaced, not silently passed.
    assert "runtime:model_bindings" not in names
    assert not any(name.startswith("models:forecastops:") for name in names)
    assert report["dev_admin"]["operations"] == [
        "anonymous_denied",
        "invalid_password_refused",
        "password_login",
        "session_read",
        "operator_bootstrap",
        "wrong_role_denied",
        "job_enqueue_and_readback",
        "cross_tenant_denied",
        "audit_readback",
        "logout_and_revocation",
    ]
    assert report["release_profile"] == {
        "name": "dev-admin",
        "model_readiness_claimed": False,
        "full_product_acceptance_claimed": False,
    }
    # Every write and the logout carried the Web origin (CSRF), and the
    # wrong-role probe really asked for the denied role.
    sent = dict(web.headers_seen)
    assert sent["POST /auth/logout"]["origin"] == base.WEB_URL
    assert any(
        call == "GET /api/v1/operator/bootstrap" and h.get("x-operator-role") == DENIED_ROLE
        for call, h in web.headers_seen
    )


def test_the_report_never_contains_the_admin_password() -> None:
    _, report, _ = run_dev_admin(
        web=RoleAwareWeb(web_routes(**{"anon POST /login": base.response(503, {"echo": PASSWORD})}))
    )

    assert PASSWORD not in json.dumps(report)


# ---------------------------------------------------------------------------
# The full profile keeps rejecting the same runtime
# ---------------------------------------------------------------------------


def test_full_profile_still_rejects_a_runtime_without_production_models() -> None:
    api = api_routes(**{"anon GET /readiness": base.response(200, missing_model_readiness("full"))})
    _, report = base.run_gate(api, cfg=base.disabled_config())

    found = blockers(report)
    assert found["runtime:model_bindings"] == "mlflow"
    assert found["runtime:model_capability:forecastops"] == "mlflow"
    assert found["models:registry"] == "mlflow"
    assert "session:password_login" not in found  # no session journey in full scope


def test_dev_admin_with_verified_models_is_held_to_the_full_model_assertions() -> None:
    readiness = deepcopy(base.disabled_readiness_payload())
    readiness["details"]["releaseProfile"] = {
        "name": "dev-admin",
        "valid": True,
        "modelReadinessClaimed": False,
        "error": None,
    }
    api = api_routes(
        **{
            "anon GET /readiness": base.response(200, readiness),
            "GET /api/v1/learninghub/models": base.response(200, {"items": [], "count": 0}),
        }
    )

    _, report, _ = run_dev_admin(api=api)

    assert "models:forecastops:production_alias" in blockers(report)


# ---------------------------------------------------------------------------
# Scope negatives -- refused before any request
# ---------------------------------------------------------------------------


class NoRequests:
    def request(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"gate made a request with invalid scope: {args}")


@pytest.mark.parametrize("deployment", ["staging", "production"])
def test_dev_admin_scope_for_staging_or_production_is_refused_before_any_request(
    deployment: str,
) -> None:
    checks, report = gate.evaluate_gate(
        dev_admin_config(expected_deployment=deployment),
        http=NoRequests(),
        worker_driver=base.FakeWorkerDriver(),
        correlation_id=base.CORRELATION_ID,
        now=base.NOW,
        web_http=NoRequests(),
    )

    assert blockers(report)["config:release_profile_deployment"] == "release-profile"
    assert report["ok"] is False


@pytest.mark.parametrize("profile", ["admin", "DEV_ADMIN", "", "dev-admin-lite"])
def test_an_unknown_profile_is_refused_before_any_request(profile: str) -> None:
    _, report = gate.evaluate_gate(
        dev_admin_config(release_profile=profile),
        http=NoRequests(),
        worker_driver=base.FakeWorkerDriver(),
        correlation_id=base.CORRELATION_ID,
        now=base.NOW,
        web_http=NoRequests(),
    )

    assert blockers(report)["config:release_profile"] == "release-profile"


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"dev_admin_password": ""}, "config:dev_admin_account"),
        ({"dev_admin_username": ""}, "config:dev_admin_account"),
        ({"dev_admin_denied_role": ""}, "config:dev_admin_denied_role"),
    ],
)
def test_dev_admin_without_its_sign_in_inputs_is_refused(overrides: dict, check: str) -> None:
    _, report = gate.evaluate_gate(
        dev_admin_config(**overrides),
        http=NoRequests(),
        worker_driver=base.FakeWorkerDriver(),
        correlation_id=base.CORRELATION_ID,
        now=base.NOW,
        web_http=NoRequests(),
    )

    assert check in blockers(report)


@pytest.mark.parametrize(
    ("runtime_profile", "configured"),
    [
        ({"name": "full", "valid": True, "modelReadinessClaimed": True}, "dev-admin"),
        ({"name": "dev-admin", "valid": True, "modelReadinessClaimed": False}, "full"),
        ({"name": "dev-admin", "valid": False, "modelReadinessClaimed": False}, "dev-admin"),
        ({"name": "dev-admin", "valid": True, "modelReadinessClaimed": True}, "dev-admin"),
        (None, "dev-admin"),
    ],
)
def test_a_runtime_profile_that_is_not_the_admitted_one_blocks(
    runtime_profile: dict | None, configured: str
) -> None:
    readiness = missing_model_readiness()
    if runtime_profile is None:
        readiness["details"].pop("releaseProfile")
    else:
        readiness["details"]["releaseProfile"] = {**runtime_profile, "error": None}
    api = api_routes(**{"anon GET /readiness": base.response(200, readiness)})
    cfg = dev_admin_config() if configured == "dev-admin" else base.disabled_config()

    _, report, _ = run_dev_admin(api=api, cfg=cfg)

    assert blockers(report)["runtime:release_profile"] == "release-profile"


# ---------------------------------------------------------------------------
# Missing models must be refused truthfully
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda m: m.update(autoSeeded=True), "runtime:model_limitation_reported"),
        (lambda m: m.update(error=None), "runtime:model_limitation_reported"),
        (lambda m: m.update(blockingReasons=[]), "runtime:model_limitation_reported"),
        (lambda m: m.update(mode="mlflow-production"), "runtime:model_limitation_reported"),
        (
            lambda m: m["capabilities"]["forecastops"].update(reasonCode=None),
            "runtime:model_capability:forecastops",
        ),
        (
            lambda m: m["capabilities"]["forecastops"].update(available=True),
            "runtime:model_capability:forecastops",
        ),
        (
            lambda m: m["capabilities"]["avm"].update(available=True),
            "runtime:model_capability:avm",
        ),
    ],
)
def test_a_manufactured_model_state_blocks_dev_admin(mutate, check: str) -> None:
    readiness = missing_model_readiness()
    mutate(readiness["details"]["models"])
    api = api_routes(**{"anon GET /readiness": base.response(200, readiness)})

    _, report, _ = run_dev_admin(api=api)

    assert blockers(report)[check] == "mlflow"


@pytest.mark.parametrize(
    ("registry", "dependency"),
    [
        (base.response(200, base.models_payload()), "mlflow"),
        (base.response(200, {"items": [], "count": 0}), "mlflow"),
        (base.response(403, {"error": {"code": "forbidden"}}), "auth"),
        (base.response(0, error="URLError for /api/v1/learninghub/models"), "mlflow"),
    ],
)
def test_a_registry_that_does_not_refuse_blocks_dev_admin(registry: Any, dependency: str) -> None:
    _, report, _ = run_dev_admin(api=api_routes(**{"GET /api/v1/learninghub/models": registry}))

    assert blockers(report)["models:registry_refused"] == dependency


# ---------------------------------------------------------------------------
# Administration negatives -- each breaks exactly one fact of the journey
# ---------------------------------------------------------------------------

DB_DOWN = base.response(
    503, {"error": {"code": "WEB_AUTH_UNAVAILABLE", "summary": "temporarily unavailable"}}
)


@pytest.mark.parametrize(
    ("overrides", "check", "dependency"),
    [
        pytest.param(
            {"anon GET /auth/session": base.response(200, {"subject": "anyone"})},
            "session:anonymous_session_denied",
            "session",
            id="anonymous-session-served",
        ),
        pytest.param(
            {"anon GET /api/v1/operator/bootstrap": operator_bootstrap()},
            "session:anonymous_api_denied",
            "auth",
            id="anonymous-api-served",
        ),
        pytest.param(
            {
                "anon POST /login": lambda body, headers: base.response(
                    200, {"ok": True, "subject": USERNAME}, cookies={SESSION_COOKIE: SESSION_VALUE}
                )
            },
            "session:invalid_credentials_refused",
            "session",
            id="wrong-password-accepted",
        ),
        pytest.param(
            {"anon POST /login": DB_DOWN},
            "session:password_login",
            "session",
            id="session-store-db-down",
        ),
        pytest.param(
            {
                "anon POST /login": lambda body, headers: (
                    base.response(200, {"ok": True, "subject": USERNAME})
                    if body.get("password") == PASSWORD
                    else base.response(401, {"error": {"code": "AUTH_INVALID_CREDENTIALS"}})
                )
            },
            "session:password_login",
            "session",
            id="login-without-session-cookie",
        ),
        pytest.param(
            {
                "GET /auth/session [session]": base.response(
                    200, {"subject": "someone-else", "expiresAt": 1}
                )
            },
            "session:session_resolves_account",
            "session",
            id="session-for-another-account",
        ),
        pytest.param(
            {"GET /api/v1/operator/bootstrap [session]": DB_DOWN},
            "session:operator_bootstrap",
            "data-binding",
            id="bootstrap-db-down",
        ),
        pytest.param(
            {
                "POST /api/v1/jobs [session]": lambda body, headers: base.response(
                    202,
                    {
                        "job_id": SESSION_JOB_ID,
                        "created": True,
                        "audit_event_id": "evt",
                        "job": {"job_id": SESSION_JOB_ID},
                    },
                )
            },
            "session:cross_tenant_denied",
            "tenant-isolation",
            id="foreign-tenant-write-accepted",
        ),
        pytest.param(
            {
                "POST /api/v1/jobs [session]": lambda body, headers: base.response(
                    503, {"error": {"code": "DURABLE_JOB_QUEUE_UNAVAILABLE"}}
                )
            },
            "session:job_enqueue",
            "worker",
            id="job-persistence-failed",
        ),
        pytest.param(
            {
                f"GET /api/v1/jobs/{SESSION_JOB_ID} [session]": base.response(
                    404, {"detail": "job not found"}
                )
            },
            "session:job_readback",
            "postgresql",
            id="job-not-durable",
        ),
        pytest.param(
            {
                f"GET /api/v1/audit/events?correlation_id={SESSION_CORRELATION_ID} [session]": (
                    base.response(200, {"events": session_audit()["events"][:1]})
                )
            },
            "session:audit_persisted",
            "audit",
            id="denial-not-audited",
        ),
        pytest.param(
            {
                f"GET /api/v1/audit/events?correlation_id={SESSION_CORRELATION_ID} [session]": (
                    base.response(
                        200,
                        {
                            "events": [
                                {**session_audit()["events"][0], "integrity": {}},
                                session_audit()["events"][1],
                            ]
                        },
                    )
                )
            },
            "session:audit_persisted",
            "audit",
            id="audit-not-hash-chained",
        ),
        pytest.param(
            {
                f"GET /api/v1/audit/events?correlation_id={SESSION_CORRELATION_ID} [session]": (
                    DB_DOWN
                )
            },
            "session:audit_persisted",
            "audit",
            id="audit-store-down",
        ),
        pytest.param(
            {"POST /auth/logout [session]": DB_DOWN},
            "session:logout",
            "session",
            id="logout-not-durable",
        ),
    ],
)
def test_a_broken_administration_fact_blocks_dev_admin(
    overrides: dict, check: str, dependency: str
) -> None:
    _, report, _ = run_dev_admin(web=RoleAwareWeb(web_routes(**overrides)))

    assert report["ok"] is False
    assert blockers(report).get(check) == dependency, report["blockers"]


def test_a_role_outside_the_account_grants_that_is_served_blocks() -> None:
    routes = web_routes(**{"role-check": "disabled"})
    _, report, _ = run_dev_admin(web=RoleAwareWeb(routes))

    assert blockers(report)["session:wrong_role_denied"] == "auth"


def test_a_session_that_survives_logout_blocks() -> None:
    routes = web_routes(
        **{"GET /auth/session [session]": base.response(200, {"subject": USERNAME, "expiresAt": 1})}
    )
    _, report, _ = run_dev_admin(web=RoleAwareWeb(routes, revoke_on_logout=False))

    found = blockers(report)
    assert found["session:revoked_session_refused"] == "session"
    assert found["session:revoked_api_refused"] == "session"


def test_failed_postgresql_blocks_dev_admin_like_full() -> None:
    readiness = missing_model_readiness()
    readiness["details"]["persistence"]["reachable"] = False
    api = api_routes(**{"anon GET /readiness": base.response(200, readiness)})

    _, report, _ = run_dev_admin(api=api)

    assert blockers(report)["runtime:persistence"] == "postgresql"


def test_missing_web_client_blocks_the_session_journey() -> None:
    checks, report = base.run_gate(api_routes(), cfg=dev_admin_config(), web_http=None)

    assert "session:web_client" in blockers(report)


# ---------------------------------------------------------------------------
# The runtime the gate reads: real API composition, no fixture
# ---------------------------------------------------------------------------


def _real_readiness(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[int, dict[str, Any]]:
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    from apps.api.oday_api.main import create_app
    from shared.infrastructure.persistence.factory import _memory_bundle

    for name in (
        "MLFLOW_TRACKING_URI",
        "ODP_PRODUCTION_PROVIDER_IDS",
        "ODP_RELEASE_PROFILE",
        "ODP_DEPLOY_ENV",
        "ODAY_ENV",
        "ODP_ENV",
        "ODP_PRODUCT_MODE",
        "ODP_EXTERNAL_PROVIDER_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app(persistence=_memory_bundle())
    response = TestClient(app).get("/readiness")
    return response.status_code, response.json()


def test_the_real_runtime_reports_missing_models_the_way_dev_admin_requires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot the real API with no MLflow binding under the dev-admin profile.

    The model and profile blocks the gate judges are the ones the deployed
    composition emits, so this is what proves the truthful architecture is
    reused rather than re-described: the same runtime satisfies the dev-admin
    limitation checks and fails the full binding check.
    """

    _, body = _real_readiness(
        monkeypatch,
        ODP_REQUIRE_LIVE_DATA="true",
        ODP_DEPLOY_ENV="dev",
        ODP_EXTERNAL_PROVIDER_MODE="disabled",
        ODP_RELEASE_PROFILE="dev-admin",
    )
    details = body["details"]
    assert details["releaseProfile"] == {
        "name": "dev-admin",
        "valid": True,
        "modelReadinessClaimed": False,
        "error": None,
    }

    models = details["models"]
    limitation: list[Any] = []
    gate._check_model_limitation(models, checks=limitation)
    gate._check_model_capabilities(models, refused_in_scope=True, checks=limitation)
    assert all(check.ok for check in limitation), [c for c in limitation if not c.ok]
    assert models["capabilities"]["forecastops"]["available"] is False

    strict: list[Any] = []
    gate._check_model_bindings(models, checks=strict)
    gate._check_model_capabilities(models, refused_in_scope=False, checks=strict)
    assert {c.name for c in strict if not c.ok} >= {
        "runtime:model_bindings",
        "runtime:model_capability:forecastops",
    }


@pytest.mark.parametrize(
    "env",
    [
        {"ODP_DEPLOY_ENV": "production", "ODP_RELEASE_PROFILE": "dev-admin"},
        {"ODP_DEPLOY_ENV": "staging", "ODP_RELEASE_PROFILE": "dev-admin"},
        {"ODP_DEPLOY_ENV": "dev", "ODP_RELEASE_PROFILE": "admin"},
    ],
)
def test_the_real_runtime_fails_readiness_on_a_profile_it_may_not_serve(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    status, body = _real_readiness(monkeypatch, **env)

    assert status == 503
    assert body["details"]["releaseProfile"]["valid"] is False
    assert body["details"]["releaseProfile"]["modelReadinessClaimed"] is False


def test_the_real_runtime_defaults_to_the_full_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    status, body = _real_readiness(monkeypatch)

    assert status == 200
    assert body["details"]["releaseProfile"] == {
        "name": "full",
        "valid": True,
        "modelReadinessClaimed": True,
        "error": None,
    }


def test_cli_reads_the_profile_and_account_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_RELEASE_PROFILE", "dev-admin")
    assert gate.parse_args([]).release_profile == "dev-admin"
    monkeypatch.delenv("ODP_RELEASE_PROFILE")
    assert gate.parse_args([]).release_profile == "full"
