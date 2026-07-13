"""FastAPI authorization dependencies.

These adapt the framework-agnostic :class:`shared.auth.AuthorizationEngine` to
HTTP request handling:

- :func:`principal_from_headers` establishes the caller's
  :class:`Principal`. When the live auth boundary is configured
  (``ODP_AUTH_*``), it delegates to :class:`modules.opsboard.auth.
  AuthenticationBoundary` — cryptographically verifying a bearer JWT
  (signature/issuer/audience/expiry) or a service credential (ODP-SD-09 §3,
  ODP-GAP-AUTH-001) and failing **closed**. When no live IdP inputs are
  configured, it falls back to the legacy header-trust stub (``x-subject-id`` /
  ``x-roles``) for local dev / tests.
- :func:`require_permission` / :func:`require_feature_flag` produce FastAPI
  dependencies that deny with HTTP 403 and leave a security audit trail.

Authentication failures (untrusted / expired / missing credentials once the
boundary is configured) surface as HTTP **401** — distinct from a 403
authorization denial. RBAC/ABAC logic is unchanged; only how the principal is
established becomes real.

FastAPI is imported lazily so this module is importable without the dependency
installed (the runtime backend task adds it). When FastAPI is absent, the
returned dependencies are plain callables that still enforce policy and raise
:class:`AuthorizationError` (403) or
:class:`modules.opsboard.auth.AuthenticationError` (401) on denial.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from modules.opsboard.auth import AuthenticationBoundary
    from modules.opsboard.auth.errors import AuthFailureReason

from shared.audit.events import InMemoryAuditLog
from shared.audit.policy import AuditRecorder
from shared.auth import (
    AccessRequest,
    Action,
    AuthorizationEngine,
    DataClassification,
    Decision,
    Environment,
    FeatureFlagRegistry,
    Principal,
    ResourceDescriptor,
    Role,
    Scope,
    default_registry,
    rbac_allows,
)

try:  # pragma: no cover - exercised only when FastAPI is installed
    from fastapi import HTTPException, Request
except ModuleNotFoundError:  # pragma: no cover - lean env
    HTTPException = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]


class AuthorizationError(Exception):
    """Raised on denial when FastAPI's HTTPException is unavailable."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


def build_engine(
    *,
    audit_log: AuditRecorder | None = None,
    flags: FeatureFlagRegistry | None = None,
) -> AuthorizationEngine:
    """Construct an engine; defaults are safe for tests/local dev."""

    return AuthorizationEngine(
        audit_log=audit_log or InMemoryAuditLog(),
        flags=flags or default_registry(),
    )


def _split(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


# Lazily-built, process-wide auth boundary derived from the environment.
# ``_UNSET`` distinguishes "not resolved yet" from a resolved ``None`` (no live
# IdP inputs configured -> legacy header-trust stays active), so we build it at
# most once.
_UNSET: object = object()
_default_boundary: object = _UNSET


def default_boundary() -> AuthenticationBoundary | None:
    """Return the env-configured :class:`AuthenticationBoundary`, or ``None``.

    ``None`` means the live IdP inputs (``ODP_AUTH_ISSUER`` / ``ODP_AUTH_AUDIENCES``
    / signing keys) are absent, so this environment keeps the legacy
    header-trust behaviour. Built once and cached; call
    :func:`reset_default_boundary` after mutating ``ODP_AUTH_*`` (e.g. in tests)
    to force a rebuild.
    """

    global _default_boundary
    if _default_boundary is _UNSET:
        from modules.opsboard.auth import AuthenticationBoundary
        from modules.opsboard.auth.config import config_from_env

        config = config_from_env()
        _default_boundary = (
            AuthenticationBoundary(config) if config.is_configured else None
        )
    return _default_boundary  # type: ignore[return-value]


def reset_default_boundary() -> None:
    """Clear the cached default boundary (test/reload hook)."""

    global _default_boundary
    _default_boundary = _UNSET


def principal_from_headers(
    headers: Mapping[str, str], *, boundary: AuthenticationBoundary | None = None
) -> Principal:
    """Establish the request principal.

    When an auth boundary is active (the ``boundary`` argument or the
    env-configured default), the request's credentials — an
    ``Authorization: Bearer <jwt>`` token or a service identity — are verified
    by :class:`modules.opsboard.auth.AuthenticationBoundary` (ODP-SD-09 §3,
    ODP-GAP-AUTH-001). A verified credential yields an authenticated principal;
    any authentication failure (untrusted/expired token, or — since the
    configured boundary fails closed — no credentials at all) raises HTTP 401.

    When no boundary is configured, the legacy header-trust stub is used so
    local dev and the existing integration tests are unaffected.
    """

    boundary = boundary if boundary is not None else default_boundary()
    if boundary is not None:
        from modules.opsboard.auth import Credentials

        outcome = boundary.authenticate(Credentials.from_headers(headers))
        if outcome.authenticated:
            return outcome.principal
        _raise_unauthenticated(outcome.reason)

    return _principal_from_trusted_headers(headers)


def _principal_from_trusted_headers(headers: Mapping[str, str]) -> Principal:
    """Legacy header-trust principal (dev/test, or no live boundary configured).

    Header names are lowercase to match FastAPI/Starlette. Missing subject ->
    unauthenticated principal (ODP-AC-AUTH-001).
    """

    subject = headers.get("x-subject-id")
    if not subject:
        from shared.auth import ANONYMOUS

        return ANONYMOUS

    roles: set[Role] = set()
    for raw in _split(headers.get("x-roles")):
        try:
            roles.add(Role(raw))
        except ValueError:
            continue  # unknown role string is ignored, not trusted

    scope = Scope(
        tenant_id=headers.get("x-tenant-id"),
        brand_ids=_split(headers.get("x-brand-ids")),
        region_ids=_split(headers.get("x-region-ids")),
        store_ids=_split(headers.get("x-store-ids")),
    )
    return Principal(subject_id=subject, roles=frozenset(roles), scope=scope)


def authorize_request(
    engine: AuthorizationEngine,
    principal: Principal,
    action: Action,
    resource: ResourceDescriptor,
    *,
    source_ip: str | None = None,
    on: date | None = None,
) -> Decision:
    """Evaluate one request and raise on denial (after audit is recorded)."""

    request = AccessRequest(
        principal=principal,
        action=action,
        resource=resource,
        environment=Environment(source_ip=source_ip),
    )
    decision = engine.authorize(request, on=on)
    if not decision.allowed:
        _raise_forbidden(decision)
    return decision


def _raise_forbidden(decision: Decision) -> None:
    if HTTPException is not None:
        raise HTTPException(status_code=403, detail=decision.reason)
    raise AuthorizationError(decision)


def _raise_unauthenticated(reason: AuthFailureReason | None) -> None:
    """Reject with HTTP 401 (or the boundary's AuthenticationError in lean envs).

    The stable :class:`~modules.opsboard.auth.errors.AuthFailureReason` value is
    surfaced as the detail; it never contains token material.
    """

    from modules.opsboard.auth.errors import AuthenticationError, AuthFailureReason

    reason = reason if reason is not None else AuthFailureReason.NO_CREDENTIALS
    if HTTPException is not None:
        raise HTTPException(
            status_code=401,
            detail=reason.value,
            headers={"WWW-Authenticate": "Bearer"},
        )
    raise AuthenticationError(reason)


def require_permission(
    resource_type: str,
    action: Action,
    *,
    data_classification: DataClassification = DataClassification.CONFIDENTIAL,
    engine: AuthorizationEngine | None = None,
    boundary: AuthenticationBoundary | None = None,
):
    """FastAPI dependency factory enforcing RBAC on a route.

    This guards a route at the **type level**: it answers only "does the
    caller's role permit ``action`` on ``resource_type``" (RBAC, ODP-SA-04 §6).
    A denial returns HTTP 403 and writes a security audit event
    (ODP-AC-AUTH-005 / "403 paths write security audit events").

    Object-level policy is intentionally *not* evaluated here. Resource-instance
    attributes (tenant/region/store scope, proposer identity, risk level) are
    unknown at dependency time, so scope ABAC and the high-risk feature-flag /
    separation-of-duties hooks (SD-09 §5, which fail closed without object
    context) are enforced inside the handler and the domain workflow once the
    target object is loaded — call :func:`authorize_request` there for the full
    engine evaluation. Running the whole engine at type level would deny every
    high-risk verb (approve/execute/publish/override/rollback) unconditionally.
    """

    active_engine = engine or build_engine()

    def dependency(request: Request) -> Principal:  # type: ignore[name-defined]
        from shared.audit.policy import build_security_event

        principal = principal_from_headers(request.headers, boundary=boundary)
        if rbac_allows(principal, resource_type, action):
            return principal

        source_ip = request.client.host if request.client else None
        decision = Decision.deny(
            f"role does not permit {action.value} on {resource_type}",
            policy_id="rbac",
        )
        access = AccessRequest(
            principal=principal,
            action=action,
            resource=ResourceDescriptor(
                type=resource_type, data_classification=data_classification
            ),
            environment=Environment(source_ip=source_ip),
        )
        active_engine.audit_log.record(build_security_event(access, decision))
        _raise_forbidden(decision)

    return dependency


def require_feature_flag(key: str, *, flags: FeatureFlagRegistry | None = None):
    """FastAPI dependency factory gating a route on a feature flag."""

    registry = flags or default_registry()

    def dependency() -> None:
        if not registry.is_enabled(key, on=date.today()):
            decision = Decision.deny(
                f"feature flag {key!r} is disabled", policy_id="feature_flag"
            )
            _raise_forbidden(decision)

    return dependency


def known_roles(values: Iterable[str]) -> frozenset[Role]:
    """Parse role strings, dropping unknown values (helper for callers)."""

    parsed: set[Role] = set()
    for value in values:
        try:
            parsed.add(Role(value))
        except ValueError:
            continue
    return frozenset(parsed)
