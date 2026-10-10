"""The dev-admin release profile of the live E2E gate (ODP-DEV-ADMIN-RELEASE-READINESS-001).

Starts from the fully passing deployment of ``test_live_e2e_gate`` and changes
exactly what the dev-admin scope is about: production models are absent (and
must stay truthfully refused), and administration is proven by the single pure
platform_admin Web password sign-in and first-password rotation journey.
Every negative breaks one fact and asserts the gate names it. The full profile
is re-run against the same missing-model runtime to prove it still refuses.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any
from unittest.mock import Mock

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

USERNAME = "first.admin"
PASSWORD = "rotated-first-admin-passphrase"
INITIAL_PASSWORD = "initial-bootstrap-secret-passphrase"
DENIED_ROLE = "cs-lead"
SESSION_COOKIE = "__Host-oday_web_session"
SESSION_VALUE = "sealed-admin-session-reference"
FRESH_SESSION_VALUE = "sealed-fresh-admin-session-reference"
ADMIN_ACCOUNT_ID = "5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
ADMIN_TENANT_ID = "11111111-1111-4111-8111-111111111111"
SESSION_CORRELATION_ID = f"{base.CORRELATION_ID}-session"
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


def admin_users(roles: list[str] | None = None) -> dict[str, Any]:
    return {
        "users": [
            {
                "subject_id": ADMIN_ACCOUNT_ID,
                "username": USERNAME,
                "email": None,
                "name": USERNAME,
                "roles": roles if roles is not None else ["platform_admin"],
                "scope": admin_scope(),
                "attributes": {"identity_source": "identity.accounts", "username": USERNAME},
                "status": "active",
            }
        ],
        "count": 1,
    }


def admin_scope(tenant_id: str = ADMIN_TENANT_ID) -> dict[str, Any]:
    """The scope shape IdentityUserRoleManagementService._to_record returns."""
    return {
        "tenant_id": tenant_id,
        "brand_ids": [],
        "region_ids": [],
        "store_ids": [],
        "assigned_area_ids": [],
        "heat_zone_ids": [],
        "modules": [],
        "clearance": "CONFIDENTIAL",
    }


def tenant_policy_refusal(tenant_id: str) -> Any:
    """What users_roles.save_user returns for UserRolePolicyError on a foreign tenant."""
    return base.response(
        422,
        {
            "detail": (
                f"Cannot save user scope for tenant '{tenant_id}'; "
                "caller is restricted to its own tenant."
            )
        },
    )


def save_user_route(body: Any, headers: dict) -> Any:
    tenant = ((body or {}).get("scope") or {}).get("tenant_id")
    if tenant != ADMIN_TENANT_ID:
        return tenant_policy_refusal(tenant)
    return base.response(200, {"ok": True, "message": "User saved."})


def admin_audit_trail() -> dict[str, Any]:
    return {
        "events": [
            {
                "event_type": "identity.account.bootstrap",
                "actor": "identity-bootstrap",
                "metadata": {"account_id": ADMIN_ACCOUNT_ID, "roles": ["platform_admin"]},
            }
        ],
        "count": 1,
    }


def web_routes(**overrides: Any) -> dict[str, Any]:
    denied = base.response(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})
    routes: dict[str, Any] = {
        "anon GET /operator": base.response(
            302, location=f"{base.WEB_URL}/login?returnTo=%2Foperator"
        ),
        "anon GET /auth/session": denied,
        "anon GET /api/v1/operator/bootstrap": denied,
        "GET /auth/session [session]": base.response(200, {"subject": USERNAME, "expiresAt": 1}),
        "GET /api/v1/operator/users [session]": base.response(200, admin_users()),
        "POST /api/v1/operator/users [session]": save_user_route,
        "GET /api/v1/operator/users/audit-trail [session]": base.response(200, admin_audit_trail()),
        "GET /api/v1/operator/bootstrap [session]": base.response(
            403, {"detail": "role does not permit view on operator_console"}
        ),
        "GET /operator?view=admin [session]": base.response(200, {}),
        "POST /auth/logout [session]": base.response(
            200, {"ok": True}, cookies={SESSION_COOKIE: ""}
        ),
    }
    routes.update(overrides)
    return routes


class AdminWeb(base.FakeHttp):
    """Fake Web client that models the pure platform_admin journey:
    supports fresh accounts (first password rotation needed) and already-rotated accounts.
    """

    def __init__(
        self,
        routes: dict[str, Any],
        *,
        is_fresh: bool = False,
        revoke_on_logout: bool = True,
    ) -> None:
        super().__init__(routes)
        self.is_fresh = is_fresh
        self.revoke_on_logout = revoke_on_logout
        self.logged_out = False
        self.password_rotated = not is_fresh
        self.headers_seen: list[tuple[str, dict[str, str]]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
        self.headers_seen.append((f"{method} {path}", headers))
        assert kwargs.get("authenticated") is False, "web requests never inject app identity"
        assert "authorization" not in headers and "x-tenant-id" not in headers
        cookie = headers.get("cookie", "")
        has_session = f"{SESSION_COOKIE}={SESSION_VALUE}" in cookie
        has_fresh_session = f"{SESSION_COOKIE}={FRESH_SESSION_VALUE}" in cookie

        if self.revoke_on_logout and (has_session or has_fresh_session) and self.logged_out:
            return base.response(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})

        # POST /login
        if method == "POST" and path == "/login":
            assert headers.get("origin") == base.WEB_URL, "login must carry Web origin (CSRF)"
            if headers.get("content-type") == "application/x-www-form-urlencoded":
                assert headers.get("accept") == "text/html"
                assert kwargs.get("follow_redirects") is False
                body = kwargs.get("body") or {}
                if "form POST /login" in self.routes:
                    route = self.routes["form POST /login"]
                    return route(body, headers) if callable(route) else deepcopy(route)
                if body.get("username") == USERNAME and body.get("password") == PASSWORD and self.password_rotated:
                    self.logged_out = False
                    return base.response(303, location=f"{base.WEB_URL}/operator?view=admin",
                                         cookies={SESSION_COOKIE: SESSION_VALUE})
                return base.response(303, location=f"{base.WEB_URL}/login?error=AUTH_INVALID_CREDENTIALS&returnTo=%2Foperator%3Fview%3Dadmin")
            if "anon POST /login" in self.routes:
                route = self.routes["anon POST /login"]
                if callable(route):
                    return route(kwargs.get("body"), headers)
                return deepcopy(route)
            body = kwargs.get("body") or {}
            u = body.get("username")
            p = body.get("password")
            if u == USERNAME and p == PASSWORD and self.password_rotated:
                self.logged_out = False
                return base.response(
                    200,
                    {"ok": True, "subject": USERNAME, "returnTo": "/operator?view=admin"},
                    cookies={SESSION_COOKIE: SESSION_VALUE},
                )
            if u == USERNAME and p == INITIAL_PASSWORD and not self.password_rotated:
                self.logged_out = False
                return base.response(
                    200,
                    {"ok": True, "subject": USERNAME, "returnTo": "/operator?view=admin"},
                    cookies={SESSION_COOKIE: FRESH_SESSION_VALUE},
                )
            return base.response(
                401,
                {"error": {"code": "AUTH_INVALID_CREDENTIALS", "summary": "Invalid username or password."}},
            )

        # POST /auth/password
        if method == "POST" and path == "/auth/password":
            assert headers.get("origin") == base.WEB_URL, "password change must carry Web origin"
            if "POST /auth/password [session]" in self.routes:
                route = self.routes["POST /auth/password [session]"]
                if callable(route):
                    return route(kwargs.get("body"), headers)
                return deepcopy(route)
            body = kwargs.get("body") or {}
            curr = body.get("currentPassword")
            new_p = body.get("newPassword")
            if (curr == INITIAL_PASSWORD or (curr == PASSWORD and not self.password_rotated)) and new_p == PASSWORD:
                self.password_rotated = True
                return base.response(200, {"ok": True})
            return base.response(400, {"error": {"code": "INVALID_PASSWORD"}})

        # POST /auth/logout
        if method == "POST" and path == "/auth/logout":
            assert headers.get("origin") == base.WEB_URL, "logout must carry Web origin"
            self.logged_out = True
            if "POST /auth/logout [session]" in self.routes:
                route = self.routes["POST /auth/logout [session]"]
                if callable(route):
                    return route(kwargs.get("body"), headers)
                return deepcopy(route)
            return base.response(200, {"ok": True}, cookies={SESSION_COOKIE: ""})

        # GET /auth/session
        if method == "GET" and path == "/auth/session":
            if has_session:
                if "GET /auth/session [session]" in self.routes:
                    route = self.routes["GET /auth/session [session]"]
                    if callable(route):
                        return route(kwargs.get("body"), headers)
                    return deepcopy(route)
                return base.response(200, {"subject": USERNAME, "expiresAt": 1})
            if has_fresh_session:
                return base.response(200, {"subject": USERNAME, "expiresAt": 1})
            if "anon GET /auth/session" in self.routes:
                route = self.routes["anon GET /auth/session"]
                if callable(route):
                    return route(kwargs.get("body"), headers)
                return deepcopy(route)
            return base.response(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})

        # GET /api/v1/operator/users
        if method == "GET" and path == "/api/v1/operator/users":
            if has_fresh_session and not self.password_rotated:
                if "must-change-override" in self.routes:
                    route = self.routes["must-change-override"]
                    if callable(route):
                        return route(kwargs.get("body"), headers)
                    return deepcopy(route)
                return base.response(403, {"detail": "PASSWORD_CHANGE_REQUIRED"})
            if has_session or (has_fresh_session and self.password_rotated):
                if "GET /api/v1/operator/users [session]" in self.routes:
                    route = self.routes["GET /api/v1/operator/users [session]"]
                    if callable(route):
                        return route(kwargs.get("body"), headers)
                    return deepcopy(route)
                return base.response(200, admin_users())
            return base.response(401, {"error": {"code": "WEB_SESSION_REQUIRED"}})

        # POST /api/v1/operator/users
        if method == "POST" and path == "/api/v1/operator/users":
            if "POST /api/v1/operator/users [session]" in self.routes:
                route = self.routes["POST /api/v1/operator/users [session]"]
                if callable(route):
                    return route(kwargs.get("body"), headers)
                return deepcopy(route)
            return save_user_route(kwargs.get("body"), headers)

        # RBAC wrong-role check on /api/v1/operator/bootstrap
        if (
            (has_session or has_fresh_session)
            and path == "/api/v1/operator/bootstrap"
            and headers.get("x-operator-role") == DENIED_ROLE
        ):
            if "role-check" in self.routes and self.routes["role-check"] == "disabled":
                return base.response(200, {"status": "ok"})
            return base.response(403, {"error": {"code": "forbidden"}})

        # Dispatch other configured routes
        marker = "session" if (has_session or has_fresh_session) else "anon"
        key = f"{method.upper()} {path} [{marker}]"
        if key in self.routes:
            self.calls.append(key)
            route = self.routes[key]
            if callable(route):
                route = route(kwargs.get("body"), headers)
            return deepcopy(route)

        anon_key = f"anon {method.upper()} {path}"
        if anon_key in self.routes:
            self.calls.append(anon_key)
            route = self.routes[anon_key]
            if callable(route):
                route = route(kwargs.get("body"), headers)
            return deepcopy(route)

        return super().request(method, path, **kwargs)


def dev_admin_config(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "release_profile": "dev-admin",
        "dev_admin_username": USERNAME,
        "dev_admin_password": PASSWORD,
        "dev_admin_initial_password": "",
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
    web_http = web if web is not None else AdminWeb(web_routes())
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


def test_dev_admin_passes_with_missing_models_and_already_rotated_session() -> None:
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
        "session:html_form_invalid_redirect",
        "session:html_form_password_login",
        "session:html_form_session_resolves_account",
        "admin:html_form_admin_page_served",
        "session:html_form_logout",
        "session:html_form_revoked_session_refused",
        "session:password_login",
        "session:session_resolves_account",
        "admin:identity_user_list",
        "admin:bootstrap_audited",
        "session:cross_tenant_denied",
        "admin:foreign_tenant_unmodified_readback",
        "admin:business_shell_denied",
        "session:wrong_role_denied",
        "admin:admin_page_served",
        "session:logout",
        "session:revoked_session_refused",
        "admin:logout_revokes_admin_api",
    } <= names
    # The full-profile model assertions are replaced, not silently passed.
    assert "runtime:model_bindings" not in names
    assert not any(name.startswith("models:forecastops:") for name in names)
    assert report["dev_admin"]["operations"] == [
        "anonymous_denied",
        "invalid_password_refused",
        "password_login",
        "session_read",
        "identity_user_list",
        "bootstrap_audit_readback",
        "cross_tenant_denied",
        "business_shell_denied",
        "wrong_role_denied",
        "admin_page",
        "logout_and_revocation",
        "html_form_login_and_revocation",
    ]
    assert report["release_profile"] == {
        "name": "dev-admin",
        "model_readiness_claimed": False,
        "full_product_acceptance_claimed": False,
    }
    sent = dict(web.headers_seen)
    assert sent["POST /auth/logout"]["origin"] == base.WEB_URL
    assert any(
        call == "GET /api/v1/operator/bootstrap" and h.get("x-operator-role") == DENIED_ROLE
        for call, h in web.headers_seen
    )


@pytest.mark.parametrize("origin", ["https://0.0.0.0:3000", "https://attacker.example"])
def test_browser_form_failure_with_wrong_origin_blocks_dev_admin(origin: str) -> None:
    def login(body, headers):
        if body.get("username") == USERNAME:
            return base.response(303, location=f"{base.WEB_URL}/operator?view=admin",
                                 cookies={SESSION_COOKIE: SESSION_VALUE})
        return base.response(303, location=f"{origin}/login?error=AUTH_INVALID_CREDENTIALS&returnTo=%2Foperator%3Fview%3Dadmin")

    _, report, _ = run_dev_admin(web=AdminWeb(web_routes(**{"form POST /login": login})))
    assert report["ok"] is False
    assert "session:html_form_invalid_redirect" in blockers(report)


@pytest.mark.parametrize("location,cookies", [
    ("https://0.0.0.0:3000/operator?view=admin", {SESSION_COOKIE: SESSION_VALUE}),
    (f"{base.WEB_URL}/operator?view=admin", {}),
    (f"{base.WEB_URL}/operator?view=business", {SESSION_COOKIE: SESSION_VALUE}),
])
def test_broken_browser_form_success_blocks_dev_admin(location: str, cookies: dict) -> None:
    def login(body, headers):
        if body.get("username") == USERNAME:
            return base.response(303, location=location, cookies=cookies)
        return base.response(303, location=f"{base.WEB_URL}/login?error=AUTH_INVALID_CREDENTIALS&returnTo=%2Foperator%3Fview%3Dadmin")

    _, report, _ = run_dev_admin(web=AdminWeb(web_routes(**{"form POST /login": login})))
    assert report["ok"] is False
    assert "session:html_form_password_login" in blockers(report)


def test_form_transport_sends_urlencoded_body_without_following_redirects(monkeypatch) -> None:
    import io
    from email.message import Message

    captured = {}
    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            headers = Message()
            headers["Location"] = f"{base.WEB_URL}/login?error=AUTH_INVALID_CREDENTIALS"
            raise gate.urllib.error.HTTPError(request.full_url, 303, "See Other", headers, io.BytesIO(b""))

    def opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(gate.urllib.request, "build_opener", opener)
    client = gate.UrllibHttpClient(base.WEB_URL, timeout=5, bearer_token="", operator_role="",
                                 operator_subject="", operator_tenant="", correlation_id=base.CORRELATION_ID)
    response = client.request("POST", "/login", authenticated=False,
        body={"username": "test", "password": "a+b & 測試", "returnTo": "/operator?view=admin"},
        headers={"accept": "text/html", "content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False)
    request = captured["request"]
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert gate.urllib.parse.parse_qs(request.data.decode()) == {
        "username": ["test"], "password": ["a+b & 測試"], "returnTo": ["/operator?view=admin"]}
    assert captured["handlers"] == (gate._NoRedirect,)
    assert response.status == 303


def test_dev_admin_passes_fresh_bootstrap_account_with_controlled_rotation() -> None:
    web = AdminWeb(web_routes(), is_fresh=True)
    cfg = dev_admin_config(dev_admin_initial_password=INITIAL_PASSWORD)
    checks, report, _ = run_dev_admin(web=web, cfg=cfg)

    assert report["ok"] is True, report["blockers"]
    names = {check.name for check in checks}
    assert "session:must_change_enforced" in names
    assert "session:first_login_password_rotated" in names
    assert "session:password_login" in names
    assert report["dev_admin"]["operations"] == [
        "anonymous_denied",
        "invalid_password_refused",
        "password_rotated_and_logged_in",
        "session_read",
        "identity_user_list",
        "bootstrap_audit_readback",
        "cross_tenant_denied",
        "business_shell_denied",
        "wrong_role_denied",
        "admin_page",
        "logout_and_revocation",
        "html_form_login_and_revocation",
    ]


def test_the_report_never_contains_the_admin_password() -> None:
    _, report, _ = run_dev_admin(
        web=AdminWeb(web_routes(**{"anon POST /login": base.response(503, {"echo": PASSWORD})}))
    )

    assert PASSWORD not in json.dumps(report)
    assert INITIAL_PASSWORD not in json.dumps(report)


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
    assert "session:password_login" not in found


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


def bound_registry_readiness() -> dict[str, Any]:
    payload = missing_model_readiness()
    payload["details"]["models"]["learninghubRegistryBound"] = True
    return payload


@pytest.mark.parametrize(
    "registry",
    [
        base.response(200, {"items": [], "count": 0}),
        registry_refused(),
    ],
)
def test_a_bound_registry_with_no_versions_passes_dev_admin(registry: Any) -> None:
    """Dev with a real MLflow binding and no models yet answers an empty 200."""

    _, report, _ = run_dev_admin(
        api=api_routes(
            **{
                "anon GET /readiness": base.response(200, bound_registry_readiness()),
                "GET /api/v1/learninghub/models": registry,
            }
        )
    )

    assert "models:registry_refused" not in blockers(report)


@pytest.mark.parametrize(
    ("registry", "dependency"),
    [
        (base.response(200, base.models_payload()), "mlflow"),
        (base.response(200, {"items": [], "count": 1}), "mlflow"),
        (base.response(403, {"error": {"code": "forbidden"}}), "auth"),
    ],
)
def test_a_bound_registry_serving_versions_still_blocks_dev_admin(
    registry: Any, dependency: str
) -> None:
    _, report, _ = run_dev_admin(
        api=api_routes(
            **{
                "anon GET /readiness": base.response(200, bound_registry_readiness()),
                "GET /api/v1/learninghub/models": registry,
            }
        )
    )

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
            {"anon GET /api/v1/operator/bootstrap": base.response(200, {})},
            "session:anonymous_api_denied",
            "auth",
            id="anonymous-api-served",
        ),
        pytest.param(
            {
                "anon POST /login": lambda body, headers: (
                    base.response(200, {"ok": True, "subject": USERNAME}, cookies={SESSION_COOKIE: SESSION_VALUE})
                    if "-live-gate-invalid" in str((body or {}).get("password"))
                    else base.response(200, {"ok": True, "subject": USERNAME}, cookies={SESSION_COOKIE: SESSION_VALUE})
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
            {"GET /api/v1/operator/users [session]": base.response(200, admin_users(["growth_lead"]))},
            "admin:identity_user_list",
            "auth",
            id="user-has-wrong-roles",
        ),
        pytest.param(
            {"GET /api/v1/operator/users [session]": base.response(200, {"users": [], "count": 0})},
            "admin:identity_user_list",
            "auth",
            id="user-not-listed",
        ),
        pytest.param(
            {"GET /api/v1/operator/users/audit-trail [session]": base.response(200, {"events": []})},
            "admin:bootstrap_audited",
            "audit",
            id="bootstrap-event-missing",
        ),
        pytest.param(
            {"GET /api/v1/operator/bootstrap [session]": base.response(200, {})},
            "admin:business_shell_denied",
            "auth",
            id="business-shell-allowed-to-pure-admin",
        ),
        pytest.param(
            {"GET /operator?view=admin [session]": base.response(302, location=f"{base.WEB_URL}/login")},
            "admin:admin_page_served",
            "session",
            id="admin-page-redirects-to-login",
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
    _, report, _ = run_dev_admin(web=AdminWeb(web_routes(**overrides)))

    assert report["ok"] is False
    assert blockers(report).get(check) == dependency, report["blockers"]


def test_a_role_outside_the_account_grants_that_is_served_blocks() -> None:
    routes = web_routes(**{"role-check": "disabled"})
    _, report, _ = run_dev_admin(web=AdminWeb(routes))

    assert blockers(report)["session:wrong_role_denied"] == "auth"


def test_a_session_that_survives_logout_blocks() -> None:
    routes = web_routes(
        **{"GET /auth/session [session]": base.response(200, {"subject": USERNAME, "expiresAt": 1})}
    )
    _, report, _ = run_dev_admin(web=AdminWeb(routes, revoke_on_logout=False))

    found = blockers(report)
    assert found["session:revoked_session_refused"] == "session"
    assert found["admin:logout_revokes_admin_api"] == "session"


def test_fresh_admin_fails_if_initial_password_missing() -> None:
    web = AdminWeb(web_routes(), is_fresh=True)
    cfg = dev_admin_config(dev_admin_initial_password="")
    _, report, _ = run_dev_admin(web=web, cfg=cfg)

    assert report["ok"] is False
    assert "session:password_login" in blockers(report)


def test_fresh_admin_fails_if_rotation_fails() -> None:
    web = AdminWeb(
        web_routes(**{"POST /auth/password [session]": base.response(500, {"error": "failed"})}),
        is_fresh=True,
    )
    cfg = dev_admin_config(dev_admin_initial_password=INITIAL_PASSWORD)
    _, report, _ = run_dev_admin(web=web, cfg=cfg)

    assert report["ok"] is False
    assert "session:first_login_password_rotated" in blockers(report)


def test_fresh_admin_fails_if_must_change_not_enforced() -> None:
    web = AdminWeb(
        web_routes(**{"must-change-override": base.response(200, admin_users())}),
        is_fresh=True,
    )
    cfg = dev_admin_config(dev_admin_initial_password=INITIAL_PASSWORD)
    _, report, _ = run_dev_admin(web=web, cfg=cfg)

    assert report["ok"] is False
    assert "session:must_change_enforced" in blockers(report)
    # Critical: no password mutation must occur when must_change is not enforced
    assert web.password_rotated is False
    assert not any(call == "POST /auth/password" for call, _ in web.headers_seen)


def test_first_login_fails_without_rotation_if_initial_and_final_passwords_are_equal() -> None:
    web = AdminWeb(web_routes(), is_fresh=True)
    cfg = dev_admin_config(
        dev_admin_password=INITIAL_PASSWORD,
        dev_admin_initial_password=INITIAL_PASSWORD,
    )
    _, report, _ = run_dev_admin(web=web, cfg=cfg)

    assert report["ok"] is False
    assert "session:must_change_enforced" in blockers(report)
    assert web.password_rotated is False
    assert not any(call == "POST /auth/password" for call, _ in web.headers_seen)


def test_direct_login_with_pending_must_change_refuses_equal_input_rotation() -> None:
    # Direct login with PASSWORD succeeds, but user probe returns PASSWORD_CHANGE_REQUIRED
    web = AdminWeb(
        web_routes(
            **{
                "GET /api/v1/operator/users [session]": base.response(
                    403, {"detail": "PASSWORD_CHANGE_REQUIRED"}
                )
            }
        ),
        is_fresh=False,
    )
    # No distinct initial password configured; direct login uses PASSWORD
    cfg = dev_admin_config(dev_admin_initial_password="")
    _, report, _ = run_dev_admin(web=web, cfg=cfg)

    assert report["ok"] is False
    assert "session:must_change_enforced" in blockers(report)
    assert not any(call == "POST /auth/password" for call, _ in web.headers_seen)


class RecordingAdminWeb(AdminWeb):
    """AdminWeb that records POST /api/v1/operator/users bodies and lets a test
    rewrite the user list read *after* the tenant probe was sent."""

    def __init__(self, routes: dict[str, Any], *, after_probe: Any = None) -> None:
        super().__init__(routes)
        self.post_bodies: list[dict[str, Any]] = []
        self.after_probe = after_probe

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = super().request(method, path, **kwargs)
        if method == "POST" and path == "/api/v1/operator/users":
            self.post_bodies.append(deepcopy(kwargs.get("body") or {}))
        if (
            self.after_probe is not None
            and self.post_bodies
            and method == "GET"
            and path == "/api/v1/operator/users"
            and response.status == 200
        ):
            for user in response.payload.get("users", []):
                self.after_probe(user)
        return response


def test_tenant_probe_is_a_well_formed_move_to_a_valid_foreign_tenant() -> None:
    web = RecordingAdminWeb(web_routes())
    _, report, _ = run_dev_admin(web=web)

    assert report["ok"] is True, report["blockers"]
    [probe] = web.post_bodies
    probe_tenant = probe["scope"]["tenant_id"]
    assert str(gate.UUID(probe_tenant)) == probe_tenant
    assert probe_tenant != ADMIN_TENANT_ID
    # Only the tenant differs from the current record.
    assert {**probe["scope"], "tenant_id": ADMIN_TENANT_ID} == admin_scope()
    assert probe["subjectId"] == ADMIN_ACCOUNT_ID
    assert probe["roles"] == ["platform_admin"]
    assert probe["status"] == "active"


def test_tenant_probe_picks_another_uuid_when_admin_owns_the_probe_tenant() -> None:
    assert gate._foreign_tenant_for(gate.FOREIGN_TENANT_PROBE_ID) != gate.FOREIGN_TENANT_PROBE_ID
    assert gate._canonical_uuid(gate._foreign_tenant_for(gate.FOREIGN_TENANT_PROBE_ID))


def test_foreign_tenant_scope_allowed_blocks_dev_admin() -> None:
    web = AdminWeb(
        web_routes(
            **{
                "POST /api/v1/operator/users [session]": base.response(
                    200, {"ok": True, "message": "foreign tenant accepted"}
                )
            }
        )
    )
    _, report, _ = run_dev_admin(web=web)

    assert report["ok"] is False
    assert blockers(report)["session:cross_tenant_denied"] == "tenant-isolation"
    assert "cross_tenant_denied" not in report["dev_admin"]["operations"]


@pytest.mark.parametrize(
    "refusal",
    [
        pytest.param(base.response(400, {"error": "unrelated malformed request"}), id="unrelated400"),
        pytest.param(
            base.response(403, {"error": {"code": "WEB_CSRF_ORIGIN_REJECTED"}}), id="csrf403"
        ),
        pytest.param(
            base.response(
                422,
                {"detail": [{"loc": ["body", "roles"], "msg": "Field required", "type": "missing"}]},
            ),
            id="schema422",
        ),
        pytest.param(
            base.response(422, {"detail": "Invalid role 'platform_admin'. Must be one of canonical roles."}),
            id="other_policy422",
        ),
        pytest.param(tenant_policy_refusal("some-other-tenant"), id="policy422_for_other_tenant"),
    ],
)
def test_refusal_not_from_tenant_policy_blocks_dev_admin(refusal: Any) -> None:
    web = AdminWeb(web_routes(**{"POST /api/v1/operator/users [session]": refusal}))
    _, report, _ = run_dev_admin(web=web)

    assert report["ok"] is False
    assert blockers(report)["session:cross_tenant_denied"] == "tenant-isolation"


def _set_tenant(tenant: str) -> Any:
    def mutate(user: dict[str, Any]) -> None:
        user["scope"]["tenant_id"] = tenant

    return mutate


def _drop(*path: str) -> Any:
    def mutate(user: dict[str, Any]) -> None:
        target = user
        for key in path[:-1]:
            target = target[key]
        target.pop(path[-1], None)

    return mutate


def _set(key: str, value: Any) -> Any:
    def mutate(user: dict[str, Any]) -> None:
        user[key] = value

    return mutate


def _set_scope(key: str, value: Any) -> Any:
    def mutate(user: dict[str, Any]) -> None:
        user["scope"][key] = value

    return mutate


@pytest.mark.parametrize(
    "after_probe",
    [
        pytest.param(_set_tenant(gate.FOREIGN_TENANT_PROBE_ID), id="moved_to_probe_tenant"),
        pytest.param(_set_tenant("22222222-2222-4222-8222-222222222222"), id="changed_tenant"),
        pytest.param(_drop("scope", "tenant_id"), id="missing_tenant"),
        pytest.param(_drop("scope"), id="missing_scope"),
        pytest.param(_set_scope("store_ids", ["store-1"]), id="changed_scope_axis"),
        pytest.param(_set_scope("clearance", "RESTRICTED"), id="changed_clearance"),
        pytest.param(_set("roles", ["platform_admin", "cs-lead"]), id="changed_roles"),
        pytest.param(_set("status", "disabled"), id="changed_status"),
        pytest.param(_drop("status"), id="missing_status"),
        pytest.param(_set("username", "someone.else"), id="changed_username"),
        pytest.param(_set("subject_id", "00000000-0000-4000-8000-000000000000"), id="account_gone"),
    ],
)
def test_changed_or_missing_readback_blocks_dev_admin(after_probe: Any) -> None:
    web = RecordingAdminWeb(web_routes(), after_probe=after_probe)
    _, report, _ = run_dev_admin(web=web)

    assert web.post_bodies, "the real tenant probe must have been attempted"
    assert report["ok"] is False
    assert blockers(report)["admin:foreign_tenant_unmodified_readback"] == "tenant-isolation"
    assert "cross_tenant_denied" not in report["dev_admin"]["operations"]


@pytest.mark.parametrize(
    "users",
    [
        pytest.param(
            {"users": [{k: v for k, v in admin_users()["users"][0].items() if k != "scope"}]},
            id="no_scope",
        ),
        pytest.param(
            {"users": [{**admin_users()["users"][0], "scope": admin_scope("tenant-default")}]},
            id="placeholder_tenant",
        ),
    ],
)
def test_unknown_own_tenant_refuses_without_probing(users: dict[str, Any]) -> None:
    web = RecordingAdminWeb(
        web_routes(**{"GET /api/v1/operator/users [session]": base.response(200, users)})
    )
    _, report, _ = run_dev_admin(web=web)

    assert report["ok"] is False
    assert blockers(report)["session:cross_tenant_denied"] == "tenant-isolation"
    assert web.post_bodies == []


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
# Explicit read-enabled administration (same configured account/credentials)
# ---------------------------------------------------------------------------


def read_admin_routes(roles: list[str]) -> dict[str, Any]:
    record = admin_users(roles)["users"][0]
    audit = admin_audit_trail()
    audit["events"].append({
        "event_type": "identity.account.roles_updated",
        "metadata": {"account_id": ADMIN_ACCOUNT_ID, "tenant_id": ADMIN_TENANT_ID,
                     "roles_after": sorted(roles), "scope_after": admin_scope(), "status": "active"},
    })
    routes = web_routes(**{
        "GET /api/v1/operator/users [session]": base.response(200, admin_users(roles)),
        "GET /api/v1/auth/principal [session]": base.response(200, {
            "account_id": ADMIN_ACCOUNT_ID, "tenant_id": ADMIN_TENANT_ID, "roles": sorted(roles),
        }),
        "GET /api/v1/operator/users/audit-trail [session]": base.response(200, audit),
        f"GET /api/v1/operator/users/{ADMIN_ACCOUNT_ID} [session]": base.response(200, record),
        "GET /api/v1/operator/bootstrap [session]": base.response(200, {
            "data_mode": "live", "data_source": "postgresql://operator-live",
        }),
    })
    for path in ("network-listings/intake/submit", "network-reviews/live-gate-no-such-review/decide",
                 "network-scoring/score", "governance/decisions"):
        routes[f"POST /api/v1/operator/{path} [session]"] = base.response(403, {"detail": "role denied"})
    return routes


@pytest.mark.parametrize("roles", [
    ["platform_admin", "auditor"], ["platform_admin", "operator_viewer"],
    ["platform_admin", "auditor", "operator_viewer"],
])
def test_explicit_read_admin_requires_positive_reads_and_negative_mutations(roles: list[str]) -> None:
    checks, report, _ = run_dev_admin(web=AdminWeb(read_admin_routes(roles)))
    assert report["ok"], report["blockers"]
    assert report["dev_admin"]["account_mode"] == "read-enabled-admin"
    names = {c.name for c in checks}
    assert "admin:business_shell_denied" not in names
    assert {"admin:read_principal_bound", "admin:read_grant_audited", "admin:scoped_business_read",
            "admin:scoped_account_read", "admin:business_write_denied", "admin:business_approve_denied",
            "admin:business_execute_denied", "admin:business_publish_denied"} <= names
    assert report["release_profile"]["full_product_acceptance_claimed"] is False


@pytest.mark.parametrize("extra", ["unknown", "architecture_owner", "expansion_user", "site_reviewer",
                                   "operations_manager", "executive", "model_owner", "platform_admin"])
def test_read_admin_additional_or_duplicate_roles_fail_closed(extra: str) -> None:
    _, report, _ = run_dev_admin(web=AdminWeb(read_admin_routes(["platform_admin", "auditor", extra])))
    assert "admin:identity_user_list" in blockers(report)


@pytest.mark.parametrize("field,value", [
    ("account_id", "another-account"), ("tenant_id", gate.FOREIGN_TENANT_PROBE_ID),
    ("tenant_id", None), ("roles", ["platform_admin"]),
    ("roles", ["platform_admin", "auditor", "executive"]),
])
def test_read_admin_principal_must_bind_account_tenant_and_exact_roles(field: str, value: Any) -> None:
    routes = read_admin_routes(["platform_admin", "auditor"])
    routes["GET /api/v1/auth/principal [session]"].payload[field] = value
    _, report, _ = run_dev_admin(web=AdminWeb(routes))
    assert "admin:read_principal_bound" in blockers(report)


@pytest.mark.parametrize("field,value", [
    ("account_id", "another-account"), ("tenant_id", gate.FOREIGN_TENANT_PROBE_ID),
    ("roles_after", ["platform_admin"]), ("scope_after", {}), ("status", "disabled"),
])
def test_read_admin_requires_matching_explicit_audit(field: str, value: Any) -> None:
    routes = read_admin_routes(["platform_admin", "auditor"])
    routes["GET /api/v1/operator/users/audit-trail [session]"].payload["events"][-1]["metadata"][field] = value
    _, report, _ = run_dev_admin(web=AdminWeb(routes))
    assert "admin:read_grant_audited" in blockers(report)


@pytest.mark.parametrize("path,check", [
    ("operator/bootstrap", "admin:scoped_business_read"),
    (f"operator/users/{ADMIN_ACCOUNT_ID}", "admin:scoped_account_read"),
    ("operator/network-listings/intake/submit", "admin:business_write_denied"),
    ("operator/network-reviews/live-gate-no-such-review/decide", "admin:business_approve_denied"),
    ("operator/network-scoring/score", "admin:business_execute_denied"),
    ("operator/governance/decisions", "admin:business_publish_denied"),
])
def test_read_admin_each_required_read_and_denial_blocks(path: str, check: str) -> None:
    routes = read_admin_routes(["platform_admin", "auditor"])
    method = "GET" if "read" in check else "POST"
    routes[f"{method} /api/v1/{path} [session]"] = base.response(503 if method == "GET" else 422, {})
    _, report, _ = run_dev_admin(web=AdminWeb(routes))
    assert check in blockers(report)


@pytest.mark.parametrize("roles", [
    ["platform_admin", "auditor"], ["platform_admin", "operator_viewer"],
    ["platform_admin", "auditor", "operator_viewer"],
])
@pytest.mark.parametrize("bypass_guard", [False, True], ids=["real-guard", "broken-guard"])
def test_read_admin_gate_mutation_probes_never_reach_real_handlers(
    monkeypatch: pytest.MonkeyPatch, roles: list[str], bypass_guard: bool,
) -> None:
    """Feed the gate's actual bodies through real DTOs, routers and RBAC guards.

    The bypass models a permission regression, not an expected live operation.
    Validation must stop all handlers/services and fail the gate with 422.
    """
    from fastapi import FastAPI
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient

    from apps.api.app.routes.operator_modules.governance import create_governance_sub_router
    from apps.api.app.routes.operator_modules.network_listings import (
        create_network_listings_sub_router,
    )
    from apps.api.app.routes.operator_modules.network_reviews import (
        create_network_review_sub_router,
    )
    from apps.api.app.routes.operator_modules.network_scoring import (
        NetworkScoringBatchPayload,
        create_network_scoring_sub_router,
    )
    from apps.api.oday_api.security import dependencies
    from modules.opsboard.application.network_scoring import NetworkScoringService
    from shared.audit import InMemoryAuditLog
    from shared.auth import Action, AuthorizationEngine, Principal, Role, Scope

    # This is exactly why the old {} scoring probe was unsafe.
    assert NetworkScoringBatchPayload.model_validate({}).candidateIds is None
    principal = Principal(ADMIN_ACCOUNT_ID, frozenset(Role(r) for r in roles),
                          Scope(tenant_id=ADMIN_TENANT_ID))
    monkeypatch.setattr(dependencies, "principal_from_headers", lambda *a, **k: principal)
    monkeypatch.setattr(dependencies, "default_boundary", lambda: None)
    audit = InMemoryAuditLog()
    engine = AuthorizationEngine(audit_log=audit)
    app = FastAPI()
    guards = []

    def guard(resource: str, action: Action) -> Any:
        dependency = dependencies.require_operator_permission(resource, action, engine=engine)
        guards.append(dependency)
        return dependency

    listing_repository = Mock(tenant_id=ADMIN_TENANT_ID)
    score_repository = Mock(tenant_id=ADMIN_TENANT_ID)
    model_runtime = Mock()
    scoring = Mock(wraps=NetworkScoringService(
        seed_fixtures=False, listing_repository=listing_repository,
        sitescore_repository=score_repository, model_runtime=model_runtime,
        tenant_id=ADMIN_TENANT_ID,
    ))
    services = [Mock(), Mock(), scoring, Mock()]
    resolvers = [Mock(return_value=service) for service in services]
    factories = [
        (create_network_listings_sub_router, "require_write_permission_fn", "listing", Action.UPDATE),
        (create_network_review_sub_router, "require_decide_permission_fn", "sitescore", Action.APPROVE),
        (create_network_scoring_sub_router, "require_write_permission_fn", "sitescore", Action.EXECUTE),
        (create_governance_sub_router, "require_decision_permission_fn", "intervention", Action.APPROVE),
    ]
    paths = {
        "/api/v1/operator/network-listings/intake/submit": "write",
        "/api/v1/operator/network-reviews/live-gate-no-such-review/decide": "approve",
        "/api/v1/operator/network-scoring/score": "execute",
        "/api/v1/operator/governance/decisions": "publish",
    }
    handlers = []

    def observe(endpoint: Any) -> Any:
        spy = Mock(wraps=endpoint)
        handlers.append(spy)

        @wraps(endpoint)
        def observed(*args: Any, **kwargs: Any) -> Any:
            return spy(*args, **kwargs)

        return observed

    for (factory, permission_arg, resource, action), service, resolver in zip(
        factories, services, resolvers, strict=True,
    ):
        router = factory(
            service, require_view_permission_fn=lambda: None,
            service_resolver=resolver, **{permission_arg: guard(resource, action)},
        )
        # Instrument before inclusion; FastAPI may compose included routers lazily.
        for route in router.routes:
            if isinstance(route, APIRoute) and (
                "/api/v1/operator" + route.path.replace("{review_id}", "live-gate-no-such-review")
            ) in paths:
                route.endpoint = observe(route.endpoint)
                route.dependant.call = route.endpoint
        app.include_router(router, prefix="/api/v1/operator")
    if bypass_guard:
        for dependency in guards:
            app.dependency_overrides[dependency] = lambda: None
    assert len(handlers) == 4
    client = TestClient(app)
    routes = read_admin_routes(roles)
    received = {}

    def send(path: str, body: Any, headers: dict[str, str]) -> Any:
        assert headers["origin"] == base.WEB_URL
        assert headers["cookie"] == f"{SESSION_COOKIE}={SESSION_VALUE}"
        response = client.post(path, json=body)
        received[path] = response
        return base.response(response.status_code, response.json())

    for path in paths:
        # Capture only the path; bodies still come from the real gate under test.
        routes[f"POST {path} [session]"] = lambda body, headers, path=path: send(path, body, headers)
    _, report, _ = run_dev_admin(web=AdminWeb(routes))
    assert set(received) == set(paths)
    assert {response.status_code for response in received.values()} == {422 if bypass_guard else 403}
    assert report["ok"] is (not bypass_guard), report["blockers"]
    if bypass_guard:
        assert {f"admin:business_{label}_denied" for label in paths.values()} <= blockers(report).keys()
        assert not audit.list_events()  # No authorization proof is fabricated.
        for response in received.values():
            assert any(error["loc"][0] == "body" for error in response.json()["detail"])
    else:
        assert len(audit.list_events()) == 4
        assert all(event.outcome == "deny" for event in audit.list_events())
    for spy in [*handlers, *resolvers, *services, listing_repository, score_repository, model_runtime]:
        assert spy.mock_calls == [], "probe must never invoke handlers, scoring, providers or persistence"
    assert report["release_profile"]["full_product_acceptance_claimed"] is False

    # Offline sensitivity control: the original {} probe WOULD reach batch
    # scoring with candidate_ids=None if authorization broke. Stub the method
    # for this control only, so no real scoring/provider/persistence executes.
    for dependency in guards:
        app.dependency_overrides[dependency] = lambda: None
    scoring.score_batch.return_value = {"offline_handler_control": True}
    control = client.post("/api/v1/operator/network-scoring/score", json={})
    assert control.status_code == 200, control.text
    assert sum(spy.call_count for spy in handlers) == 1
    resolvers[2].assert_called_once()
    scoring.score_batch.assert_called_once()
    assert scoring.score_batch.call_args.kwargs["candidate_ids"] is None
    for spy in (listing_repository, score_repository, model_runtime):
        assert spy.mock_calls == []


def test_read_admin_canonical_permission_expansion_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.auth import Action, Role
    from shared.auth.rbac import ROLE_PERMISSIONS, Permission

    monkeypatch.setitem(ROLE_PERMISSIONS, Role.AUDITOR,
                        ROLE_PERMISSIONS[Role.AUDITOR] | {Permission("listing", Action.UPDATE)})
    _, report, _ = run_dev_admin(web=AdminWeb(read_admin_routes(["platform_admin", "auditor"])))
    assert "admin:identity_user_list" in blockers(report)


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


def test_bootstrap_admin_inputs_use_the_documented_environment_names() -> None:
    assert gate.BOOTSTRAP_ADMIN_USERNAME_ENV == "ODP_DEV_BOOTSTRAP_ADMIN_USERNAME"
    assert gate.BOOTSTRAP_ADMIN_PASSWORD_ENV == "ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD"
    assert gate.DEV_ADMIN_INITIAL_PASSWORD_ENV == "ODP_DEV_ADMIN_INITIAL_PASSWORD"
    assert gate.BOOTSTRAP_SECRET_ENV == "ODP_IDENTITY_BOOTSTRAP_SECRET"


INVITATION_ID = "51c78ac4-c1b0-45da-b8c3-501c02c305d9"
INVITER_ID = "17e9cb99-db46-4a07-8e61-6bf9b22cf5d2"


def invited_admin_inputs() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    user = admin_users()["users"][0]
    user["updated_by"] = INVITER_ID
    events = [
        {"event_id": "1199ca11-e976-4b5a-a537-f43b28b0c635",
         "event_type": "identity.account.invite", "actor": INVITER_ID,
         "timestamp": "2026-10-10T05:00:00+00:00", "outcome": "success",
         "resource": f"identity.invitation:{INVITATION_ID}",
         "correlation_id": f"identity-invitation-{INVITATION_ID}",
         "metadata": {"invitation_id": INVITATION_ID, "tenant_id": ADMIN_TENANT_ID,
                      "preset_roles": ["platform_admin"], "preset_scope": admin_scope(),
                      "expires_at": "2026-10-10T06:00:00+00:00"}},
        {"event_id": "43a1c2d9-e3f5-4889-9118-6e6c65c2cd8b",
         "event_type": "identity.account.accept", "actor": ADMIN_ACCOUNT_ID,
         "timestamp": "2026-10-10T05:01:00+00:00", "outcome": "success",
         "resource": f"identity.invitation:{INVITATION_ID}",
         "correlation_id": f"identity-invitation-{INVITATION_ID}",
         "metadata": {"invitation_id": INVITATION_ID, "tenant_id": ADMIN_TENANT_ID,
                      "account_id": ADMIN_ACCOUNT_ID, "subject_id": ADMIN_ACCOUNT_ID,
                      "roles": ["platform_admin"], "scope": admin_scope(),
                      "status": "active", "must_change": False}},
    ]
    principal = {"account_id": ADMIN_ACCOUNT_ID, "tenant_id": ADMIN_TENANT_ID,
                 "roles": ["platform_admin"]}
    return user, events, principal


def run_invited_admin(user: Any, events: Any, principal: Any) -> tuple[Any, Any, Any]:
    return run_dev_admin(web=AdminWeb(web_routes(**{
        "GET /api/v1/operator/users [session]": base.response(200, {"users": [user], "count": 1}),
        "GET /api/v1/operator/users/audit-trail [session]": base.response(200, {"events": events}),
        "GET /api/v1/auth/principal [session]": base.response(200, principal),
    })))


def test_invited_pure_admin_passes_without_manufacturing_bootstrap() -> None:
    user, events, principal = invited_admin_inputs()
    checks, report, web = run_invited_admin(user, events, principal)
    assert report["ok"] is True, report["blockers"]
    assert report["dev_admin"]["account_mode"] == "pure-admin"
    assert report["dev_admin"]["identity_provenance"] == {
        "mode": "invitation", "invitation_id": INVITATION_ID,
        "issue_event_id": events[0]["event_id"], "accept_event_id": events[1]["event_id"],
        "issuer_account_id": INVITER_ID,
    }
    names = {c.name for c in checks}
    assert {"admin:invitation_audited", "admin:invitation_principal_bound",
            "admin:business_shell_denied", "admin:logout_revokes_admin_api"} <= names
    assert "admin:bootstrap_audited" not in names
    assert "invitation_audit_readback" in report["dev_admin"]["operations"]
    assert not any(method.startswith("POST") and "/invitations" in method
                   for method, _ in web.headers_seen)


@pytest.mark.parametrize("event_index,path,value", [
    (0, ("actor",), ADMIN_ACCOUNT_ID),
    (0, ("actor",), "not-a-verified-account"),
    (0, ("event_id",), None),
    (1, ("event_id",), "1199ca11-e976-4b5a-a537-f43b28b0c635"),
    (1, ("outcome",), "failure"),
    (1, ("actor",), INVITER_ID),
    (1, ("resource",), "identity.account:forged"),
    (1, ("correlation_id",), "foreign-invitation"),
    (1, ("metadata", "tenant_id"), gate.FOREIGN_TENANT_PROBE_ID),
    (1, ("metadata", "invitation_id"), "malformed"),
    (1, ("metadata", "subject_id"), INVITER_ID),
    (1, ("metadata", "must_change"), True),
    (1, ("metadata", "roles"), ["platform_admin", "operations_manager"]),
    (0, ("metadata", "preset_roles"), ["platform_admin", "auditor"]),
    (0, ("metadata", "preset_scope", "store_ids"), ["foreign-store"]),
    (1, ("metadata", "scope", "clearance"), "PUBLIC"),
    (1, ("timestamp",), "2026-10-10T06:00:00+00:00"),
    (1, ("timestamp",), "2026-10-10T04:59:59+00:00"),
    (0, ("timestamp",), "2026-10-10T05:00:00"),
    (0, ("metadata", "expires_at"), "2026-10-13T05:00:01+00:00"),
    (0, ("metadata", "expires_at"), "bad-date"),
])
def test_invitation_provenance_rejects_mismatched_or_incomplete_events(
    event_index: int, path: tuple[str, ...], value: Any,
) -> None:
    user, events, principal = invited_admin_inputs()
    target = events[event_index]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    _, report, _ = run_invited_admin(user, events, principal)
    assert report["ok"] is False
    assert blockers(report)["admin:invitation_audited"] == "audit"


@pytest.mark.parametrize("kind", ["missing_issue", "duplicate_issue", "duplicate_accept",
                                  "other_account_accept", "revoked", "fake_bootstrap", "creator_changed"])
def test_invitation_provenance_rejects_ambiguous_lifecycles(kind: str) -> None:
    user, events, principal = invited_admin_inputs()
    if kind == "missing_issue":
        events.pop(0)
    elif kind == "duplicate_issue":
        events.append(deepcopy(events[0]))
    elif kind in {"duplicate_accept", "other_account_accept"}:
        events.append(deepcopy(events[1]))
        if kind == "other_account_accept":
            events[-1]["actor"] = INVITER_ID
            events[-1]["metadata"]["account_id"] = INVITER_ID
    elif kind == "revoked":
        events.append({"event_type": "identity.account.invitation_revoked",
                       "metadata": {"invitation_id": INVITATION_ID}})
    elif kind == "fake_bootstrap":
        events.extend(admin_audit_trail()["events"])
    else:
        user["updated_by"] = ADMIN_ACCOUNT_ID
    _, report, _ = run_invited_admin(user, events, principal)
    assert report["ok"] is False
    assert blockers(report)["admin:invitation_audited"] == "audit"


@pytest.mark.parametrize("field,value", [("account_id", INVITER_ID),
                                         ("tenant_id", gate.FOREIGN_TENANT_PROBE_ID),
                                         ("roles", ["platform_admin", "operations_manager"])])
def test_invited_pure_admin_requires_cookie_to_verified_principal_binding(field: str, value: Any) -> None:
    user, events, principal = invited_admin_inputs()
    principal[field] = value
    _, report, _ = run_invited_admin(user, events, principal)
    assert report["ok"] is False
    assert blockers(report)["admin:invitation_principal_bound"] == "auth"


def credential_bundle_input() -> dict[str, Any]:
    from delivery_toolchain.release.provision_dev_smoke import AUTHORIZATION_ID, TENANT_ID
    return {"schema_version": 1, "authorization_id": AUTHORIZATION_ID,
            "execution_id": "747efb4e-230d-4864-9bb9-194bec13045b",
            "repository": "alfloop-dev/odayplus", "environment": "dev", "tenant_id": TENANT_ID,
            "account_id": ADMIN_ACCOUNT_ID, "username": USERNAME, "password": PASSWORD}


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("schema_version", 2), ("authorization_id", "other"),
    ("repository", "foreign/repo"), ("environment", "staging"),
    ("tenant_id", ADMIN_TENANT_ID), ("account_id", INVITER_ID),
    ("account_id", "not-a-uuid"), ("execution_id", "00000000-0000-0000-0000-000000000000"),
    ("username", "ajoe734"), ("username", " new.admin"), ("password", ""),
    ("password", None), ("initial_password", "private-bootstrap-never-use"),
])
def test_matched_bundle_rejects_wrong_scope_shape_or_partial_pair(key: str, value: Any) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, read_dev_credential_bundle
    raw = credential_bundle_input()
    raw[key] = value
    with pytest.raises(ProvisioningRefused, match="^PROVISIONING_CREDENTIAL_BUNDLE_INVALID$") as error:
        read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")
    assert error.value.__cause__ is None and PASSWORD not in str(error.value)


@pytest.mark.parametrize("raw", [" ", "null", "[]", "{private-password", "x" * 16385,
    '{"password":"private-duplicate","password":"another-private"}'])
def test_matched_bundle_bad_json_never_becomes_legacy(raw: str) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, read_dev_credential_bundle
    with pytest.raises(ProvisioningRefused, match="^PROVISIONING_CREDENTIAL_BUNDLE_INVALID$"):
        read_dev_credential_bundle(raw, environment="dev", release_profile="dev-admin")


def test_matched_bundle_parser_keeps_exact_password_memory_only() -> None:
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle
    raw = credential_bundle_input()
    raw["password"] = "  private exact password  "
    bundle = read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")
    assert bundle.password == raw["password"] and bundle.account_id == ADMIN_ACCOUNT_ID
    assert raw["password"] not in repr(bundle)
    assert read_dev_credential_bundle("", environment="production", release_profile="full") is None


@pytest.mark.parametrize("environment,profile", [("staging", "dev-admin"), ("dev", "full"),
                                                  ("production", "full")])
def test_matched_bundle_never_admitted_outside_dev_admin(environment: str, profile: str) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, read_dev_credential_bundle
    with pytest.raises(ProvisioningRefused):
        read_dev_credential_bundle(json.dumps(credential_bundle_input()), environment=environment,
                                   release_profile=profile)


def test_gate_cli_bundle_suppresses_every_old_password_and_username(monkeypatch: Any, tmp_path: Path) -> None:
    from delivery_toolchain.release.provision_dev_smoke import TENANT_ID
    monkeypatch.setenv("ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", json.dumps(credential_bundle_input()))
    for name in (gate.DEV_ADMIN_USERNAME_ENV, gate.DEV_ADMIN_PASSWORD_ENV, gate.DEV_ADMIN_INITIAL_PASSWORD_ENV,
                 gate.BOOTSTRAP_SECRET_ENV, gate.BOOTSTRAP_ADMIN_USERNAME_ENV, gate.BOOTSTRAP_ADMIN_PASSWORD_ENV):
        monkeypatch.setenv(name, "private-stale-binding-never-use")

    class Captured(Exception):
        pass

    def capture(config: Any, **kwargs: Any) -> Any:
        assert config.dev_admin_username == USERNAME and config.dev_admin_password == PASSWORD
        assert config.dev_admin_initial_password == config.bootstrap_admin_password == config.bootstrap_admin_username == ""
        assert config.dev_admin_bundle_account_id == ADMIN_ACCOUNT_ID
        assert config.dev_admin_bundle_tenant_id == TENANT_ID
        assert PASSWORD not in repr(config)
        raise Captured

    monkeypatch.setattr(gate, "evaluate_gate", capture)
    monkeypatch.setattr(gate, "_web_client", lambda *a: None)
    with pytest.raises(Captured):
        gate.main(["--release-profile", "dev-admin", "--expected-deployment", "dev",
                   "--output", str(tmp_path / "no-report.json")])
    assert not list(tmp_path.iterdir())


def test_gate_cli_malformed_bundle_refuses_before_network_or_output(monkeypatch: Any, tmp_path: Path, capsys: Any) -> None:
    monkeypatch.setenv("ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", "{private-password-malformed")
    monkeypatch.setattr(gate, "UrllibHttpClient", lambda *a, **k: pytest.fail("invalid bundle made a client"))
    assert gate.main(["--release-profile", "dev-admin", "--expected-deployment", "dev",
                      "--output", str(tmp_path / "no-report.json")]) == 2
    assert "private-password" not in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def bundle_journal_events() -> list[dict[str, Any]]:
    from delivery_toolchain.release.provision_dev_smoke import AUTHORIZATION_ID, ProvisioningJournal, DevCredentialBundleExecutor
    common = {"authorization_id": AUTHORIZATION_ID, "execution_id": credential_bundle_input()["execution_id"],
              "plan_digest": "sha256:" + "0" * 64, "release_sha": "b" * 40, "manifest_digest": "sha256:" + "a" * 64,
              "tenant_id": ADMIN_TENANT_ID, "execution_authorized": False, "secret_values_redacted": True}
    events = []
    for i, (cls, stage) in enumerate(((ProvisioningJournal, "reserved"),
                                     (DevCredentialBundleExecutor, "binding-intent"),
                                     (DevCredentialBundleExecutor, "binding-acknowledged"))):
        metadata = {**common, "stage": stage}
        if cls is DevCredentialBundleExecutor:
            metadata.update(account_id=ADMIN_ACCOUNT_ID, secret_name="ODP_DEV_ADMIN_CREDENTIAL_BUNDLE",
                            credential_binding_verified=False)
        events.append({"event_id": f"1199ca11-e976-4b5a-a537-f43b28b0c63{i}", "event_type": cls._TYPE,
                       "actor": cls._ACTOR, "resource": cls._CORRELATION, "correlation_id": cls._CORRELATION,
                       "action": "DEV_SMOKE_RESERVATION" if i == 0 else "DEV_SMOKE_BINDING",
                       "outcome": "success", "timestamp": f"2026-10-10T05:02:0{i}+00:00", "metadata": metadata})
    return events


@pytest.mark.parametrize("mismatch", ["none", "account", "tenant", "bootstrap", "missing_ack", "quarantine",
                                     "wrong_execution", "wrong_plan", "duplicate_ack", "claimed_verified"])
def test_gate_matches_bundle_account_and_requires_real_invitation(mismatch: str) -> None:
    user, events, principal = invited_admin_inputs()
    cfg = dev_admin_config(dev_admin_bundle_account_id=ADMIN_ACCOUNT_ID,
                           dev_admin_bundle_tenant_id=ADMIN_TENANT_ID,
                           dev_admin_bundle_execution_id=credential_bundle_input()["execution_id"])
    if mismatch in {"account", "tenant"}:
        from dataclasses import replace
        cfg = replace(cfg, **{"dev_admin_bundle_" + ("account_id" if mismatch == "account" else "tenant_id"):
                              gate.FOREIGN_TENANT_PROBE_ID})
    if mismatch == "bootstrap":
        events = admin_audit_trail()["events"]
    journals = bundle_journal_events()
    if mismatch == "missing_ack":
        journals.pop()
    elif mismatch == "quarantine":
        journals[-1]["metadata"]["stage"] = "recovery-required"
    elif mismatch == "wrong_execution":
        journals[-1]["metadata"]["execution_id"] = INVITATION_ID
    elif mismatch == "wrong_plan":
        journals[-1]["metadata"]["plan_digest"] = "sha256:" + "f" * 64
    elif mismatch == "duplicate_ack":
        journals.append(deepcopy(journals[-1]))
    elif mismatch == "claimed_verified":
        journals[-1]["metadata"]["credential_binding_verified"] = True
    events.extend(journals)
    _, report, _ = run_dev_admin(cfg=cfg, web=AdminWeb(web_routes(**{
        "GET /api/v1/operator/users [session]": base.response(200, {"users": [user], "count": 1}),
        "GET /api/v1/operator/users/audit-trail [session]": base.response(200, {"events": events}),
        "GET /api/v1/auth/principal [session]": base.response(200, principal),
    })))
    assert report["ok"] is (mismatch == "none"), report["blockers"]
    if mismatch in {"account", "tenant"}:
        assert blockers(report)["admin:credential_bundle_account_bound"] == "auth"
    elif mismatch == "bootstrap":
        assert blockers(report)["admin:credential_bundle_invitation_required"] == "audit"
    elif mismatch != "none":
        assert blockers(report)["admin:credential_bundle_durable_acknowledgement"] == "audit"
    assert PASSWORD not in json.dumps(report)
