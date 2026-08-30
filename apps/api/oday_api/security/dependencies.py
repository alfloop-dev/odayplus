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

import logging
import os
from collections.abc import Iterable, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

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

_LOGGER = logging.getLogger(__name__)


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

    The boundary is active whenever **any** live IdP input is present
    (``ODP_AUTH_ISSUER`` / ``ODP_AUTH_AUDIENCES`` / ``ODP_AUTH_HS256_KEYS``).
    ``None`` -- keeping the legacy header-trust behaviour -- is returned only
    when the environment carries *no* ``ODP_AUTH_*`` live inputs at all.

    This fails closed on a *partial* configuration: setting, say, the issuer and
    audiences but forgetting the signing keys makes the boundary active but not
    :attr:`~modules.opsboard.auth.config.AuthBoundaryConfig.is_configured`, so
    every request is denied (401) instead of silently falling back to trusting
    spoofable ``x-subject-id`` / ``x-roles`` headers (ODP-FIN-AUTH-001). A
    deployer who set any auth input clearly intends live auth; a typo must never
    downgrade to header trust.

    Built once and cached; call :func:`reset_default_boundary` after mutating
    ``ODP_AUTH_*`` (e.g. in tests) to force a rebuild.
    """

    global _default_boundary
    if _default_boundary is _UNSET:
        from modules.opsboard.auth import AuthenticationBoundary
        from modules.opsboard.auth.config import config_from_env

        # Pull durable identity/session stores from the persistence bundle so
        # that production PostgreSQL deployments use SqlIdentityStore and the
        # boundary can resolve persisted accounts and sessions (review defect
        # #1 fix: ODP-WEB-LOCAL-AUTH-API-TRUST-001).
        identity_store = None
        session_service = None
        try:
            from shared.infrastructure.persistence import build_persistence

            bundle = build_persistence()
            identity_store = getattr(bundle, "identity_store", None)
            session_service = getattr(bundle, "session_service", None)
        except Exception:
            # Persistence may not be available (e.g. missing DATABASE_URL in a
            # lean test environment). Fall back to in-memory doubles so the
            # boundary still requires sid/account instead of silently skipping.
            _LOGGER.debug(
                "default_boundary: persistence bundle unavailable; "
                "falling back to in-memory identity/session stores"
            )

        if identity_store is None or session_service is None:
            from shared.identity import (
                InMemoryIdentityStore,
                InMemorySessionRepository,
                SessionConfig,
                SessionService,
            )

            identity_store = identity_store or InMemoryIdentityStore()
            session_service = session_service or SessionService(
                repository=InMemorySessionRepository(),
                config=SessionConfig(),
            )

        config = config_from_env(
            identity_store=identity_store,
            session_service=session_service,
        )
        _default_boundary = AuthenticationBoundary(config) if config.has_live_inputs else None
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
    ODP-GAP-AUTH-001, ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §4). A verified
    credential yields an authenticated principal; any authentication failure
    raises HTTP 401.

    When ODP_PRODUCT_MODE == "production", browser-supplied spoofable headers
    are NEVER trusted under any circumstances (Contract §4.2, T16).

    When no boundary is configured in non-production mode, the legacy
    header-trust stub is used so local dev and unit tests are unaffected.
    """

    is_production = os.environ.get("ODP_PRODUCT_MODE") == "production"

    boundary = boundary if boundary is not None else default_boundary()
    if boundary is not None:
        from modules.opsboard.auth import Credentials

        outcome = boundary.authenticate(Credentials.from_headers(headers))
        if outcome.authenticated:
            return outcome.principal
        _raise_unauthenticated(outcome.reason)

    if is_production:
        from modules.opsboard.auth.errors import AuthFailureReason

        _raise_unauthenticated(AuthFailureReason.NO_CREDENTIALS)

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
        assigned_area_ids=_split(headers.get("x-assigned-area-ids")),
        heat_zone_ids=_split(headers.get("x-heat-zone-ids")),
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
    session_service: Any = None,
):
    """FastAPI dependency factory enforcing RBAC on a route.

    This guards a route at the **type level**: it answers only "does the
    caller's role permit ``action`` on ``resource_type``" (RBAC, ODP-SA-04 §6).
    Both allow and denial decisions write a security audit event (Contract §8.1, T20).
    High-risk actions immediately verify session validity (Contract §5.4, T21).
    """

    active_engine = engine or build_engine()

    def dependency(request: Request) -> Principal:  # type: ignore[name-defined]
        from modules.opsboard.auth.errors import AuthFailureReason
        from shared.audit.policy import (
            ALWAYS_AUDITED_ACTIONS,
            build_security_event,
            is_high_risk,
        )

        principal = principal_from_headers(request.headers, boundary=boundary)

        # Immediate session validation for high-risk / write actions (Contract §5.4 / T21)
        effective_session_svc = session_service
        if effective_session_svc is None and boundary is not None:
            effective_session_svc = getattr(boundary, "_session_service", None)
        if effective_session_svc is None:
            def_b = default_boundary()
            if def_b is not None:
                effective_session_svc = getattr(def_b, "_session_service", None)

        if (
            (is_high_risk(action) or action in ALWAYS_AUDITED_ACTIONS)
            and principal.authenticated
            and effective_session_svc is not None
        ):
            sid_val = principal.attributes.get("sid")
            if not sid_val:
                # A high-risk action without a session reference is denied
                # when a session layer is wired (Contract §5.4 / T21).
                _raise_unauthenticated(AuthFailureReason.SESSION_NOT_FOUND)

            from uuid import UUID

            try:
                sid_uuid = UUID(str(sid_val))
                session = effective_session_svc.validate_session(sid_uuid)
                if session is None:
                    _raise_unauthenticated(AuthFailureReason.SESSION_REVOKED)
            except (ValueError, TypeError):
                _raise_unauthenticated(AuthFailureReason.MALFORMED_TOKEN)

        source_ip = request.client.host if request.client else None
        correlation_id = _correlation_id_from_request(request)

        if rbac_allows(principal, resource_type, action):
            decision = Decision.allow(f"role permits {action.value} on {resource_type}")
            access = AccessRequest(
                principal=principal,
                action=action,
                resource=ResourceDescriptor(
                    type=resource_type,
                    tenant_id=principal.tenant_id,
                    data_classification=data_classification,
                ),
                environment=Environment(
                    source_ip=source_ip, attributes={"correlation_id": correlation_id}
                ),
            )
            try:
                active_engine.audit_log.record(build_security_event(access, decision))
            except Exception:
                _LOGGER.exception("RBAC allow audit failed; access still granted")

            request.state.operator_principal = principal
            request.state.operator_subject_id = principal.subject_id
            request.state.operator_system_roles = ",".join(
                sorted(role.value for role in principal.roles)
            )
            return principal

        decision = Decision.deny(
            f"role does not permit {action.value} on {resource_type}",
            policy_id="rbac",
        )
        access = AccessRequest(
            principal=principal,
            action=action,
            resource=ResourceDescriptor(
                type=resource_type,
                tenant_id=principal.tenant_id,
                data_classification=data_classification,
            ),
            environment=Environment(
                source_ip=source_ip, attributes={"correlation_id": correlation_id}
            ),
        )
        active_engine.audit_log.record(build_security_event(access, decision))
        _raise_forbidden(decision)

    return dependency


def require_feature_flag(key: str, *, flags: FeatureFlagRegistry | None = None):
    """FastAPI dependency factory gating a route on a feature flag."""

    registry = flags or default_registry()

    def dependency() -> None:
        if not registry.is_enabled(key, on=date.today()):
            decision = Decision.deny(f"feature flag {key!r} is disabled", policy_id="feature_flag")
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


OPERATOR_CONSOLE_RESOURCE = "operator_console"
OPERATOR_TENANT_ID = "tenant-a"

_OPERATOR_ROLE_BY_PLATFORM_ROLE: dict[Role, tuple[str, ...]] = {
    Role.PLATFORM_ADMIN: ("platform-admin", "ops-lead", "pm-audit"),
    # The CS lead console persona is a narrower operational view selected by
    # an authenticated operations manager; selecting it cannot widen grants.
    Role.OPERATIONS_MANAGER: ("ops-lead", "cs-lead"),
    Role.REGIONAL_SUPERVISOR: ("field-lead",),
    Role.MARKETING_MANAGER: ("marketing-manager",),
    # An expansion user is an individual contributor.  Manager authority is
    # granted only by a distinct verified reviewer/executive claim; the
    # caller-controlled X-Operator-Role header may select among grants but can
    # never widen them.
    Role.EXPANSION_USER: ("expansion-staff",),
    Role.SITE_REVIEWER: ("expansion-manager",),
    Role.AUDITOR: ("pm-audit",),
    Role.EXECUTIVE: ("ops-lead", "expansion-manager", "pm-audit"),
}

_OPERATOR_ROLE_ALIASES = {
    "platformadmin": "platform-admin",
    "platform-admin": "platform-admin",
    "admin": "platform-admin",
    "opslead": "ops-lead",
    "ops-lead": "ops-lead",
    "cslead": "cs-lead",
    "cs-lead": "cs-lead",
    "fieldlead": "field-lead",
    "field-lead": "field-lead",
    "facilitieslead": "field-lead",
    "marketingmanager": "marketing-manager",
    "marketing-manager": "marketing-manager",
    "growthmanager": "marketing-manager",
    "growthlead": "marketing-manager",
    "expansionmanager": "expansion-manager",
    "expansion-manager": "expansion-manager",
    "expansionstaff": "expansion-staff",
    "expansion-staff": "expansion-staff",
    "sitereviewer": "expansion-manager",
    "site-reviewer": "expansion-manager",
    "pm-audit": "pm-audit",
    "pmaudit": "pm-audit",
    "auditpm": "pm-audit",
}

_OPERATOR_ROLE_PRIORITY = (
    "platform-admin",
    "ops-lead",
    "expansion-manager",
    "expansion-staff",
    "marketing-manager",
    "field-lead",
    "pm-audit",
    "cs-lead",
)


def _normalize_operator_role(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.strip()
    if not compact:
        return None
    lowered = compact.replace("_", "-").strip().lower()
    return _OPERATOR_ROLE_ALIASES.get(lowered.replace("-", ""), _OPERATOR_ROLE_ALIASES.get(lowered))


def operator_role_ids_for(principal: Principal) -> frozenset[str]:
    """Return Operator Console role ids implied by verified platform roles."""

    roles: set[str] = set()
    for role in principal.roles:
        roles.update(_OPERATOR_ROLE_BY_PLATFORM_ROLE.get(role, ()))
    return frozenset(roles)


def _select_operator_role(
    request: Request, principal: Principal
) -> tuple[str | None, Decision | None]:  # type: ignore[name-defined]
    allowed = operator_role_ids_for(principal)
    if not allowed:
        return None, Decision.deny(
            "principal has no Operator Console role", policy_id="operator.role"
        )

    requested = _normalize_operator_role(request.headers.get("x-operator-role"))
    subject_role = None
    if principal.subject_id.startswith("operator-"):
        subject_role = _normalize_operator_role(principal.subject_id.removeprefix("operator-"))

    for candidate in (requested, subject_role):
        if candidate is None:
            continue
        if candidate not in allowed:
            return None, Decision.deny(
                "requested Operator Console role is outside principal roles",
                policy_id="operator.role_scope",
            )
        return candidate, None

    for role_id in _OPERATOR_ROLE_PRIORITY:
        if role_id in allowed:
            return role_id, None
    return sorted(allowed)[0], None


def _correlation_id_from_request(request: Request) -> str:  # type: ignore[name-defined]
    return (
        getattr(request.state, "correlation_id", None)
        or request.headers.get("x-correlation-id")
        or "unknown"
    )


def _operator_access_request(
    request: Request,  # type: ignore[name-defined]
    principal: Principal,
    action: Action,
    resource: ResourceDescriptor,
) -> AccessRequest:
    source_ip = request.client.host if request.client else None
    return AccessRequest(
        principal=principal,
        action=action,
        resource=resource,
        environment=Environment(
            source_ip=source_ip,
            attributes={"correlation_id": _correlation_id_from_request(request)},
        ),
    )


def _record_operator_denial(
    engine: AuthorizationEngine,
    access: AccessRequest,
    decision: Decision,
) -> None:
    """Record a denial without letting the recording decide the response.

    Every caller runs this immediately before _raise_forbidden/_raise_unauthenticated.
    Uncaught, an audit sink error escapes the guard instead of the 401/403: the
    caller gets a 500 that says nothing was decided, when in fact access was
    refused, and the decision reason is lost with it. Reproduced against the
    live app -- a failing sink turned a decided 403 on
    GET /operator/network-listings/intake into an unhandled RuntimeError, seen
    in CI as an intermittently failing e2e (expected 403, received 500).

    The failure stays loud: it is logged with the decision that was being
    recorded, so a silently unaudited denial is still visible in the API logs.
    What it can no longer do is convert a refusal into a server fault.
    """
    from shared.audit.policy import build_security_event

    try:
        engine.audit_log.record(build_security_event(access, decision))
    except Exception:
        _LOGGER.exception(
            "operator denial audit failed; denial still enforced "
            "(policy_id=%s reason=%s actor=%s resource=%s)",
            decision.policy_id,
            decision.reason,
            access.principal.subject_id,
            access.resource.type,
        )


def _operator_scope_decision(principal: Principal, resource: ResourceDescriptor) -> Decision:
    if resource.tenant_id and principal.tenant_id != resource.tenant_id:
        return Decision.deny(
            "Operator Console tenant scope mismatch",
            policy_id="operator.tenant_isolation",
        )
    scope = principal.scope
    for value, permits, reason, policy_id in (
        (
            resource.brand_id,
            scope.permits_brand,
            "brand outside principal scope",
            "scope.brand",
        ),
        (
            resource.region_id,
            scope.permits_region,
            "region outside principal scope",
            "scope.region",
        ),
        (
            resource.store_id,
            scope.permits_store,
            "store outside principal scope",
            "scope.store",
        ),
    ):
        # The Operator shell resource is a collection. A missing object id is
        # constrained by tenant-aware repository queries, not denied here.
        if value is not None and not permits(value):
            return Decision.deny(reason, policy_id=policy_id)
    if resource.module is not None and not scope.permits_module(resource.module):
        return Decision.deny(
            "module outside principal scope",
            policy_id="scope.module",
        )
    if not scope.permits_classification(resource.data_classification):
        return Decision.deny(
            "data classification exceeds principal clearance",
            policy_id="data_classification",
        )
    return Decision.allow("Operator Console scope accepted")


def require_operator_permission(
    resource_type: str = OPERATOR_CONSOLE_RESOURCE,
    action: Action = Action.VIEW,
    *,
    tenant_id: str | None = None,
    module: str = "operator_console",
    data_classification: DataClassification = DataClassification.CONFIDENTIAL,
    engine: AuthorizationEngine | None = None,
    boundary: AuthenticationBoundary | None = None,
    session_service: Any = None,
):
    """FastAPI dependency for Operator Console auth/RBAC/tenant isolation.

    Unlike the legacy domain guard, Operator Console endpoints distinguish
    authentication from authorization even in local header-trust mode: missing
    credentials are HTTP 401, while an authenticated principal without the
    required role/scope is HTTP 403. The guard also writes the verified
    principal and server-selected Operator role to ``request.state`` so route
    handlers do not rely on spoofable role headers.
    Both allow and denial decisions write a security audit event (Contract §8.1, T20).
    High-risk actions immediately verify session validity (Contract §5.4, T21).
    """

    active_engine = engine or build_engine()

    def dependency(request: Request) -> Principal:  # type: ignore[name-defined]
        from modules.opsboard.auth.errors import AuthFailureReason
        from shared.audit.policy import (
            ALWAYS_AUDITED_ACTIONS,
            build_security_event,
            is_high_risk,
        )

        principal = principal_from_headers(request.headers, boundary=boundary)
        effective_tenant_id = tenant_id or principal.tenant_id
        resource = ResourceDescriptor(
            type=resource_type,
            tenant_id=effective_tenant_id,
            module=module,
            data_classification=data_classification,
        )
        access = _operator_access_request(request, principal, action, resource)

        if not principal.authenticated:
            decision = Decision.deny("principal not authenticated", policy_id="authenticated")
            _record_operator_denial(active_engine, access, decision)
            _raise_unauthenticated(None)

        # High risk / session validation check (Contract §5.4 / T21)
        effective_session_svc = session_service
        if effective_session_svc is None and boundary is not None:
            effective_session_svc = getattr(boundary, "_session_service", None)
        if effective_session_svc is None:
            def_b = default_boundary()
            if def_b is not None:
                effective_session_svc = getattr(def_b, "_session_service", None)

        if (
            (is_high_risk(action) or action in ALWAYS_AUDITED_ACTIONS)
            and principal.attributes.get("sid")
            and effective_session_svc is not None
        ):
            from uuid import UUID

            try:
                sid_uuid = UUID(str(principal.attributes["sid"]))
                session = effective_session_svc.validate_session(sid_uuid)
                if session is None:
                    decision = Decision.deny(
                        "session has been revoked or expired", policy_id="session.revoked"
                    )
                    _record_operator_denial(active_engine, access, decision)
                    _raise_unauthenticated(AuthFailureReason.SESSION_REVOKED)
            except (ValueError, TypeError):
                _raise_unauthenticated(AuthFailureReason.MALFORMED_TOKEN)
        elif (
            (is_high_risk(action) or action in ALWAYS_AUDITED_ACTIONS)
            and effective_session_svc is not None
            and not principal.attributes.get("sid")
        ):
            # High-risk action without a session reference when a session
            # layer is wired → deny (Contract §5.4 / T21).
            decision = Decision.deny(
                "session reference required for high-risk action",
                policy_id="session.required",
            )
            _record_operator_denial(active_engine, access, decision)
            _raise_unauthenticated(AuthFailureReason.SESSION_NOT_FOUND)

        if not effective_tenant_id:
            decision = Decision.deny(
                "Operator Console tenant scope is required",
                policy_id="operator.tenant_isolation",
            )
            _record_operator_denial(active_engine, access, decision)
            _raise_forbidden(decision)

        selected_role, role_decision = _select_operator_role(request, principal)
        if role_decision is not None:
            _record_operator_denial(active_engine, access, role_decision)
            _raise_forbidden(role_decision)

        if not rbac_allows(principal, resource_type, action):
            decision = Decision.deny(
                f"role does not permit {action.value} on {resource_type}",
                policy_id="rbac",
            )
            _record_operator_denial(active_engine, access, decision)
            _raise_forbidden(decision)

        scope_decision = _operator_scope_decision(principal, resource)
        if not scope_decision.allowed:
            _record_operator_denial(active_engine, access, scope_decision)
            _raise_forbidden(scope_decision)

        # Audit on allow (T20)
        allow_decision = Decision.allow("Operator Console access accepted")
        try:
            active_engine.audit_log.record(build_security_event(access, allow_decision))
        except Exception:
            _LOGGER.exception(
                "operator allow audit failed; access still granted (actor=%s resource=%s)",
                access.principal.subject_id,
                access.resource.type,
            )

        request.state.operator_principal = principal
        request.state.operator_tenant_id = effective_tenant_id
        request.state.operator_role_id = selected_role
        request.state.operator_subject_id = principal.subject_id
        request.state.operator_system_roles = ",".join(
            sorted(role.value for role in principal.roles)
        )
        return principal

    return dependency
