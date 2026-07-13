"""API auth path wired to the OpsBoard auth boundary (ODP-FIN-AUTH-001).

The FastAPI security dependency (``apps/api/oday_api/security/dependencies.py``)
used to trust plaintext ``x-subject-id`` / ``x-roles`` headers. It now
delegates authentication to the real
:class:`modules.opsboard.auth.AuthenticationBoundary` (ODP-GAP-AUTH-001) when
that boundary is configured, and keeps the legacy header-trust stub only when
it is not.

These tests cover the *wiring*, not the boundary internals (which have their
own suite):

- a verified bearer token yields an authenticated principal;
- an untrusted / expired token, or (fail-closed) no credentials once the
  boundary is configured, is HTTP 401 — not a 403 authorization denial;
- RBAC is unchanged: a verified but under-privileged principal is still 403'd;
- with no boundary configured, the legacy header-trust path is preserved so
  existing routes/tests are unaffected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from modules.opsboard.auth.errors import AuthenticationError, AuthFailureReason
from shared.auth import Action, Role, rbac_allows

ISSUER = "https://idp.oday.test"
AUDIENCE = "oday-plus-api"
KEY = SigningKey(kid="k1", algorithm="HS256", secret=b"api-wiring-secret")


def _config(**overrides) -> AuthBoundaryConfig:
    base = {
        "issuer": ISSUER,
        "audiences": frozenset({AUDIENCE}),
        "signing_keys": {KEY.kid: KEY},
    }
    base.update(overrides)
    return AuthBoundaryConfig(**base)


def _boundary(**overrides) -> AuthenticationBoundary:
    return AuthenticationBoundary(_config(**overrides))


def _token(
    *,
    roles: list[str] | None = None,
    sub: str = "user-42",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    exp_delta: timedelta = timedelta(hours=1),
    key: SigningKey = KEY,
    **claims: object,
) -> str:
    now = datetime.now(UTC)
    payload: dict = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": now.timestamp(),
        "exp": (now + exp_delta).timestamp(),
    }
    if roles is not None:
        payload["roles"] = roles
    payload.update(claims)
    return encode_compact_jwt(payload, key)


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


# A 401 surfaces as FastAPI's HTTPException when installed, else the boundary's
# AuthenticationError.
_UNAUTH_EXC = (
    (deps.HTTPException, AuthenticationError)
    if deps.HTTPException is not None
    else AuthenticationError
)


def _assert_401(exc_info, expected_reason: AuthFailureReason) -> None:
    exc = exc_info.value
    if deps.HTTPException is not None and isinstance(exc, deps.HTTPException):
        assert exc.status_code == 401
        assert exc.detail == expected_reason.value
        assert exc.headers.get("WWW-Authenticate") == "Bearer"
    else:
        assert isinstance(exc, AuthenticationError)
        assert exc.reason is expected_reason


# --- principal_from_headers with an injected boundary -----------------------


def test_valid_bearer_token_yields_authenticated_principal():
    token = _token(roles=[Role.OPERATIONS_MANAGER.value], tenant_id="tenant-a")
    principal = deps.principal_from_headers(_bearer(token), boundary=_boundary())

    assert principal.authenticated is True
    assert principal.subject_id == "user-42"
    assert principal.has_role(Role.OPERATIONS_MANAGER)
    assert principal.scope.tenant_id == "tenant-a"


def test_expired_token_is_401():
    token = _token(roles=[Role.OPERATIONS_MANAGER.value], exp_delta=timedelta(hours=-1))
    with pytest.raises(_UNAUTH_EXC) as exc_info:
        deps.principal_from_headers(_bearer(token), boundary=_boundary())
    _assert_401(exc_info, AuthFailureReason.TOKEN_EXPIRED)


def test_bad_signature_is_401():
    wrong_key = SigningKey(kid="k1", algorithm="HS256", secret=b"attacker-secret")
    token = _token(roles=[Role.OPERATIONS_MANAGER.value], key=wrong_key)
    with pytest.raises(_UNAUTH_EXC) as exc_info:
        deps.principal_from_headers(_bearer(token), boundary=_boundary())
    _assert_401(exc_info, AuthFailureReason.BAD_SIGNATURE)


def test_wrong_issuer_is_401():
    token = _token(roles=[Role.OPERATIONS_MANAGER.value], issuer="https://evil.test")
    with pytest.raises(_UNAUTH_EXC) as exc_info:
        deps.principal_from_headers(_bearer(token), boundary=_boundary())
    _assert_401(exc_info, AuthFailureReason.ISSUER_MISMATCH)


def test_configured_boundary_fails_closed_without_credentials():
    # No bearer token at all -> fail closed (401), never anonymous-then-403.
    with pytest.raises(_UNAUTH_EXC) as exc_info:
        deps.principal_from_headers({}, boundary=_boundary())
    _assert_401(exc_info, AuthFailureReason.NO_CREDENTIALS)


def test_configured_boundary_ignores_legacy_headers():
    # Legacy header-trust must NOT work once the boundary is configured.
    with pytest.raises(_UNAUTH_EXC) as exc_info:
        deps.principal_from_headers(
            {"x-subject-id": "spoofed", "x-roles": Role.PLATFORM_ADMIN.value},
            boundary=_boundary(),
        )
    _assert_401(exc_info, AuthFailureReason.NO_CREDENTIALS)


def test_garbage_token_is_401():
    with pytest.raises(_UNAUTH_EXC) as exc_info:
        deps.principal_from_headers(_bearer("not-a-jwt"), boundary=_boundary())
    _assert_401(exc_info, AuthFailureReason.MALFORMED_TOKEN)


# --- backward compatibility: no boundary configured -------------------------


def test_legacy_header_path_preserved_without_boundary():
    principal = deps.principal_from_headers(
        {"x-subject-id": "op-1", "x-roles": Role.OPERATIONS_MANAGER.value},
        boundary=None,
    )
    assert principal.subject_id == "op-1"
    assert principal.has_role(Role.OPERATIONS_MANAGER)


def test_missing_subject_is_anonymous_without_boundary():
    principal = deps.principal_from_headers({}, boundary=None)
    assert principal.authenticated is False


# --- default boundary from environment --------------------------------------


def test_default_boundary_none_when_unconfigured(monkeypatch):
    for var in (
        "ODP_AUTH_ISSUER",
        "ODP_AUTH_AUDIENCES",
        "ODP_AUTH_HS256_KEYS",
        "ODP_AUTH_LEEWAY_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)
    deps.reset_default_boundary()
    try:
        assert deps.default_boundary() is None
    finally:
        deps.reset_default_boundary()


@pytest.mark.parametrize(
    "env",
    [
        {"ODP_AUTH_ISSUER": ISSUER, "ODP_AUTH_AUDIENCES": AUDIENCE},  # no keys
        {"ODP_AUTH_ISSUER": ISSUER},  # issuer only
        {"ODP_AUTH_AUDIENCES": AUDIENCE},  # audiences only
        {"ODP_AUTH_HS256_KEYS": "k1:api-wiring-secret"},  # keys only
        # Malformed keys-only: `k1` has no `kid:secret` pair, so config_from_env
        # parses zero keys. A raw-env typo must still fail closed, never drop to
        # header trust (ODP-FIN-AUTH-001 reviewer reopen).
        {"ODP_AUTH_HS256_KEYS": "k1"},
        {"ODP_AUTH_HS256_KEYS": ":no-kid"},  # empty kid also malformed
    ],
)
def test_partial_env_fails_closed_not_header_trust(monkeypatch, env):
    """A partial ODP_AUTH_* config must NOT re-enable the header-trust stub.

    Regression for ODP-FIN-AUTH-001: setting some but not all live inputs used
    to leave ``is_configured`` False, dropping the boundary to ``None`` and
    silently trusting spoofable ``x-subject-id`` / ``x-roles``. The boundary is
    now active (fail-closed) whenever any live input is present -- including a
    *malformed* ODP_AUTH_HS256_KEYS that parses to zero keys but still signals
    live-auth intent.
    """

    for var in (
        "ODP_AUTH_ISSUER",
        "ODP_AUTH_AUDIENCES",
        "ODP_AUTH_HS256_KEYS",
        "ODP_AUTH_LEEWAY_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    deps.reset_default_boundary()
    try:
        # Boundary is active despite the incomplete config.
        assert deps.default_boundary() is not None

        # A spoofed platform_admin principal is rejected, not authenticated.
        with pytest.raises(_UNAUTH_EXC) as exc_info:
            deps.principal_from_headers(
                {"x-subject-id": "spoofed", "x-roles": Role.PLATFORM_ADMIN.value}
            )
        _assert_401(exc_info, AuthFailureReason.NO_CREDENTIALS)

        # A presented bearer token also fails closed: an incomplete config
        # cannot verify anything.
        with pytest.raises(_UNAUTH_EXC) as exc_info:
            deps.principal_from_headers(_bearer(_token()))
        _assert_401(exc_info, AuthFailureReason.BOUNDARY_NOT_CONFIGURED)
    finally:
        deps.reset_default_boundary()


def test_default_boundary_built_from_env(monkeypatch):
    monkeypatch.setenv("ODP_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("ODP_AUTH_AUDIENCES", AUDIENCE)
    monkeypatch.setenv("ODP_AUTH_HS256_KEYS", "k1:api-wiring-secret")
    deps.reset_default_boundary()
    try:
        boundary = deps.default_boundary()
        assert boundary is not None
        token = _token(roles=[Role.OPERATIONS_MANAGER.value])
        # principal_from_headers with no explicit boundary picks up the default.
        principal = deps.principal_from_headers(_bearer(token))
        assert principal.subject_id == "user-42"
    finally:
        deps.reset_default_boundary()


# --- RBAC unchanged: authn establishes identity, authz still gates ----------


def test_rbac_still_denies_verified_but_underprivileged_principal():
    token = _token(roles=[Role.AUDITOR.value])
    principal = deps.principal_from_headers(_bearer(token), boundary=_boundary())
    # Auditor cannot approve a pricing action -> RBAC would 403.
    assert rbac_allows(principal, "priceops", Action.APPROVE) is False


# --- route-level through FastAPI: 401 (authn) vs 403 (authz) vs 200 ----------


def test_route_distinguishes_401_403_200():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = fastapi.FastAPI()
    guard = deps.require_permission("heatzone", Action.VIEW, boundary=_boundary())

    @app.get("/guarded", dependencies=[fastapi.Depends(guard)])
    def guarded():  # pragma: no cover - trivial handler
        return {"ok": True}

    client = TestClient(app)

    # Invalid token -> 401 authentication, with a bearer challenge.
    bad = client.get(
        "/guarded",
        headers=_bearer(
            _token(
                roles=[Role.EXPANSION_USER.value],
                key=SigningKey(kid="k1", algorithm="HS256", secret=b"wrong"),
            )
        ),
    )
    assert bad.status_code == 401
    assert bad.headers.get("www-authenticate") == "Bearer"

    # No credentials at all -> 401 (fail closed).
    assert client.get("/guarded").status_code == 401

    # Verified token but role lacks heatzone:view -> 403 authorization.
    forbidden = client.get(
        "/guarded", headers=_bearer(_token(roles=[Role.AUDITOR.value]))
    )
    assert forbidden.status_code == 403

    # Verified token with the granting role -> 200.
    ok = client.get(
        "/guarded", headers=_bearer(_token(roles=[Role.EXPANSION_USER.value]))
    )
    assert ok.status_code == 200
