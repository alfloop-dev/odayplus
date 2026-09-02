"""End-to-end: OpsBoard auth boundary -> shared RBAC/ABAC engine.

ODP-GAP-AUTH-001 delivers the *authentication* half; R0-007's
:class:`shared.auth.AuthorizationEngine` is the *authorization* half. This test
proves they compose: a cryptographically verified principal is what the engine
authorizes, and an unauthenticated (fail-closed) principal is denied before any
role check, with a shared audit trail across both stages.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    AuthFailureReason,
    Credentials,
    SigningKey,
    encode_compact_jwt,
)
from shared.audit import InMemoryAuditLog
from shared.auth import (
    AccessRequest,
    Action,
    AuthorizationEngine,
    Environment,
    ResourceDescriptor,
    Role,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
ISSUER = "https://idp.oday.example"
AUDIENCE = "oday-api"
KEY = SigningKey(kid="k1", algorithm="HS256", secret=b"integration-secret")


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def boundary(audit_log: InMemoryAuditLog) -> AuthenticationBoundary:
    # Contract §4.4: the service issuer class (and its legacy ODP_AUTH_ISSUER
    # alias, §8.4) takes roles and scope from ODP_AUTH_PRINCIPAL_MAP, never
    # from the token's own claims. The subject must therefore be declared
    # here for the boundary to authenticate it at all
    # (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
    config = AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({AUDIENCE}),
        signing_keys={KEY.kid: KEY},
        principal_mappings={
            "user-1": {
                "roles": ["operations_manager"],
                "scope": {"tenant_id": "tenant-a", "region_ids": ["north"]},
            }
        },
    )
    return AuthenticationBoundary(config, audit_log=audit_log)


@pytest.fixture
def engine(audit_log: InMemoryAuditLog) -> AuthorizationEngine:
    # Same audit log: authentication and authorization events share one trail.
    return AuthorizationEngine(audit_log=audit_log)


def _token(roles: list[str], **claims: object) -> str:
    """Build a signed token for ``user-1``.

    ``roles`` and the scope claims below are deliberately still written into
    the token: the boundary must ignore them and use the authoritative
    ODP_AUTH_PRINCIPAL_MAP declaration instead. Tests assert on the resulting
    principal, not on these claims.
    """
    payload = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": NOW.timestamp(),
        "exp": (NOW + timedelta(hours=1)).timestamp(),
        "roles": roles,
        "tenant_id": "tenant-a",
        "region_ids": ["north"],
    }
    payload.update(claims)
    return encode_compact_jwt(payload, KEY)


def _view_forecast(
    principal,
    engine,
    *,
    region_id: str | None = "north",
    brand_id: str | None = None,
    module: str | None = None,
):
    # operations_manager may VIEW forecastops (RBAC); ABAC then enforces the
    # principal's scope.
    request = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(
            type="forecastops",
            tenant_id="tenant-a",
            region_id=region_id,
            brand_id=brand_id,
            module=module,
        ),
        environment=Environment(attributes={"correlation_id": "corr-int"}),
    )
    return engine.authorize(request)


def test_verified_principal_is_authorized_by_rbac(boundary, engine):
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"])), now=NOW
    )
    assert outcome.authenticated is True

    decision = _view_forecast(outcome.principal, engine)
    assert decision.allowed is True


def test_undeclared_subject_cannot_authenticate_from_its_own_claims(boundary, engine):
    """A signed token whose sub is not in ODP_AUTH_PRINCIPAL_MAP fails closed.

    Regression for ODP-WEB-LOCAL-AUTH-API-TRUST-001: the token is validly
    signed by the trusted key and passes issuer/audience/expiry validation,
    but its `sub` has no authoritative declaration, so its self-asserted
    roles and tenant must not become authorization facts.
    """
    token = _token(["platform_admin"], sub="attacker", tenant_id="attacker-tenant")
    outcome = boundary.authenticate(Credentials(bearer_token=token), now=NOW)

    assert outcome.authenticated is False
    assert outcome.reason is AuthFailureReason.UNKNOWN_SERVICE
    assert outcome.principal.roles == frozenset()
    assert outcome.principal.tenant_id is None

    decision = _view_forecast(outcome.principal, engine)
    assert decision.allowed is False


def test_declared_subject_claim_roles_are_ignored(boundary):
    """The declared principal wins over an escalated `roles` claim."""
    token = _token(["platform_admin"], tenant_id="attacker-tenant")
    outcome = boundary.authenticate(Credentials(bearer_token=token), now=NOW)

    assert outcome.authenticated is True
    assert outcome.principal.roles == frozenset({Role.OPERATIONS_MANAGER})
    assert outcome.principal.tenant_id == "tenant-a"


def test_unauthenticated_principal_is_denied_by_engine(boundary, engine):
    # Expired token -> fail-closed ANONYMOUS principal -> engine denies.
    expired = _token(["operations_manager"], exp=(NOW - timedelta(hours=1)).timestamp())
    outcome = boundary.authenticate(Credentials(bearer_token=expired), now=NOW)
    assert outcome.authenticated is False

    decision = _view_forecast(outcome.principal, engine)
    assert decision.allowed is False


def test_verified_principal_scope_still_enforced(boundary, engine):
    # Authentication succeeds, but ABAC scope containment blocks another region.
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"])), now=NOW
    )
    decision = _view_forecast(outcome.principal, engine, region_id="south")
    assert decision.allowed is False
    assert decision.policy_id == "scope.region"


def test_verified_principal_brand_scope_enforced(engine):
    config = AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({AUDIENCE}),
        signing_keys={KEY.kid: KEY},
        principal_mappings={
            "user-1": {
                "roles": ["operations_manager"],
                "scope": {"tenant_id": "tenant-a", "brand_ids": ["brand-a"]},
            }
        },
    )
    boundary = AuthenticationBoundary(config)
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"])), now=NOW
    )
    assert outcome.authenticated is True

    decision_allowed = _view_forecast(outcome.principal, engine, brand_id="brand-a")
    assert decision_allowed.allowed is True

    decision_denied = _view_forecast(outcome.principal, engine, brand_id="brand-b")
    assert decision_denied.allowed is False
    assert decision_denied.policy_id == "scope.brand"


def test_verified_principal_module_scope_enforced(engine):
    config = AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({AUDIENCE}),
        signing_keys={KEY.kid: KEY},
        principal_mappings={
            "user-1": {
                "roles": ["operations_manager"],
                "scope": {"tenant_id": "tenant-a", "modules": ["forecastops"]},
            }
        },
    )
    boundary = AuthenticationBoundary(config)
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"])), now=NOW
    )
    assert outcome.authenticated is True

    decision_allowed = _view_forecast(outcome.principal, engine, module="forecastops")
    assert decision_allowed.allowed is True

    decision_denied = _view_forecast(outcome.principal, engine, module="netplan")
    assert decision_denied.allowed is False
    assert decision_denied.policy_id == "scope.module"


def test_authentication_and_authorization_share_audit_trail(boundary, engine, audit_log):
    outcome = boundary.authenticate(
        Credentials(bearer_token=_token(["operations_manager"]), correlation_id="corr-int"),
        now=NOW,
    )
    _view_forecast(outcome.principal, engine)

    event_types = {e.event_type for e in audit_log.list_events()}
    assert "security.authentication" in event_types


def test_operator_smoke_principal_least_privilege_composite_roles(engine):
    """ODP-OPERATOR-SMOKE-RBAC-LIVE-001: prove least-privilege composite roles.

    The dedicated smoke principal requires:
    - operations_manager -> operator_console:view (bootstrap) + audit:view
    - model_owner        -> model:view (learninghub) + audit:view
    - data_owner         -> integration:view (ingestion runs) + audit:view

    All 4 required live gate endpoints pass without platform_admin or header bypass.
    """
    composite_roles = ["operations_manager", "model_owner", "data_owner"]
    token = _token(
        composite_roles,
        sub="110296401444439097904",
        email="oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com",
    )

    config_with_map = AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({AUDIENCE}),
        signing_keys={KEY.kid: KEY},
        principal_mappings={
            "110296401444439097904": {
                "roles": composite_roles,
                "tenant_id": "tenant-a",
            },
            "oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com": {
                "roles": composite_roles,
                "tenant_id": "tenant-a",
            },
        },
    )
    mapped_boundary = AuthenticationBoundary(config_with_map)
    outcome = mapped_boundary.authenticate(Credentials(bearer_token=token), now=NOW)

    assert outcome.authenticated is True
    principal = outcome.principal
    assert principal.roles == frozenset({Role.OPERATIONS_MANAGER, Role.MODEL_OWNER, Role.DATA_OWNER})
    assert Role.PLATFORM_ADMIN not in principal.roles

    # 1. GET /api/v1/operator/bootstrap -> operator_console:view
    bootstrap_req = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="operator_console", tenant_id="tenant-a"),
        environment=Environment(attributes={"correlation_id": "smoke-1"}),
    )
    assert engine.authorize(bootstrap_req).allowed is True

    # 2. GET /api/v1/learninghub/models -> model:view
    model_req = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="model", tenant_id="tenant-a"),
        environment=Environment(attributes={"correlation_id": "smoke-2"}),
    )
    assert engine.authorize(model_req).allowed is True

    # 3. GET /api/v1/external-data/ingestion-runs -> integration:view
    ingestion_req = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="integration", tenant_id="tenant-a"),
        environment=Environment(attributes={"correlation_id": "smoke-3"}),
    )
    assert engine.authorize(ingestion_req).allowed is True

    # 4. GET /api/v1/audit/events -> audit:view
    audit_req = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="audit", tenant_id="tenant-a"),
        environment=Environment(attributes={"correlation_id": "smoke-4"}),
    )
    assert engine.authorize(audit_req).allowed is True


def test_operator_smoke_principal_single_role_operations_manager_reproduces_403(engine):
    """ODP-OPERATOR-SMOKE-RBAC-LIVE-001: root-cause reproduction of 403 failure.

    In runs 30745285034 and 30747676117, the smoke principal had only operations_manager.
    bootstrap & audit pass, but model:view and integration:view fail closed with 403.
    """
    token = _token(["operations_manager"], sub="110296401444439097904")
    config_with_map = AuthBoundaryConfig(
        issuer=ISSUER,
        audiences=frozenset({AUDIENCE}),
        signing_keys={KEY.kid: KEY},
        principal_mappings={
            "110296401444439097904": {
                "roles": ["operations_manager"],
                "tenant_id": "tenant-a",
            },
        },
    )
    mapped_boundary = AuthenticationBoundary(config_with_map)
    outcome = mapped_boundary.authenticate(Credentials(bearer_token=token), now=NOW)

    assert outcome.authenticated is True
    principal = outcome.principal
    assert principal.roles == frozenset({Role.OPERATIONS_MANAGER})

    # operator_console:view -> allowed
    bootstrap_req = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="operator_console", tenant_id="tenant-a"),
        environment=Environment(attributes={"correlation_id": "smoke-root-1"}),
    )
    assert engine.authorize(bootstrap_req).allowed is True

    # model:view -> DENIED (403)
    model_req = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="model", tenant_id="tenant-a"),
        environment=Environment(attributes={"correlation_id": "smoke-root-2"}),
    )
    assert engine.authorize(model_req).allowed is False

    # integration:view -> DENIED (403)
    ingestion_req = AccessRequest(
        principal=principal,
        action=Action.VIEW,
        resource=ResourceDescriptor(type="integration", tenant_id="tenant-a"),
        environment=Environment(attributes={"correlation_id": "smoke-root-3"}),
    )
    assert engine.authorize(ingestion_req).allowed is False


def test_business_rbac_matrix_operations_manager_remains_unwidened():
    """ODP-OPERATOR-SMOKE-RBAC-LIVE-001: operations_manager role definition is untouched.

    operations_manager must not globally gain model:view or integration:view.
    """
    from shared.auth.rbac import ROLE_PERMISSIONS, Permission

    ops_perms = ROLE_PERMISSIONS[Role.OPERATIONS_MANAGER]
    assert Permission("model", Action.VIEW) not in ops_perms
    assert Permission("integration", Action.VIEW) not in ops_perms
