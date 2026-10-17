"""Real HTTP/RBAC controls with test-signed principals, not production approvals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.oday_api.routes.feature_flags import create_feature_flags_router
from apps.api.oday_api.security import dependencies
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.audit import InMemoryAuditLog
from shared.auth.feature_flags import FeatureFlag, FeatureFlagRegistry

KEY = "unit.high-risk"
BASE = "/api/v1/admin/feature-flags"
SIGNING_KEY = SigningKey(
    kid="unit-feature-flags", algorithm="HS256", secret=b"unit-feature-flag-signing-key"
)
ISSUER = "https://unit-feature-flags.invalid"
ADMIN_REQUESTS = [
    ("GET", BASE, None),
    ("GET", BASE + "/" + KEY, None),
    ("POST", BASE, {"key": "unit.new", "owner": "configuration-owner"}),
    ("POST", BASE + "/" + KEY + "/approve", {}),
    ("POST", BASE + "/" + KEY + "/enable", {}),
    ("POST", BASE + "/" + KEY + "/disable", None),
]


COMMAND_PROOF = {"reason": "Synthetic operational command reason", "riskAcknowledged": True}


@dataclass
class FlagContext:
    client: TestClient
    registry: FeatureFlagRegistry
    audit: InMemoryAuditLog


def _headers(
    subject: str, *, correlation_id: str = "unit-request",
    issuer: str = ISSUER, signing_key: SigningKey = SIGNING_KEY,
) -> dict[str, str]:
    now = datetime.now(UTC)
    token = encode_compact_jwt(
        {
            "sub": subject,
            "iss": issuer,
            "aud": "unit-feature-api",
            "iat": now.timestamp(),
            "exp": (now + timedelta(minutes=5)).timestamp(),
            # A token's own role and these HTTP fields must not elevate the
            # principal beyond its authenticated server mapping.
            "roles": ["platform_admin"],
        },
        signing_key,
    )
    return {
        "Authorization": "Bearer " + token,
        "x-subject-id": "forged-header-admin",
        "x-roles": "platform_admin",
        "x-correlation-id": correlation_id,
    }


def _test_app(registry, audit, *, boundary):
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get(
            "x-correlation-id", "unit-server-correlation"
        )
        return await call_next(request)

    app.include_router(
        create_feature_flags_router(
            registry=registry,
            audit_log=audit,
            engine=dependencies.build_engine(audit_log=audit, flags=registry),
            boundary=boundary,
        ),
        prefix="/api/v1",
    )
    return app


@pytest.fixture
def authorized_flags(monkeypatch):
    # Even an explicit local/data-incomplete environment must not downgrade a
    # configured authentication boundary to caller-supplied approval names.
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    registry = FeatureFlagRegistry(
        {KEY: FeatureFlag(key=KEY, owner="configuration-owner", high_risk=True)}
    )
    audit = InMemoryAuditLog()
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer=ISSUER,
            audiences=frozenset({"unit-feature-api"}),
            signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
            principal_mappings={
                "admin-a": {"roles": ["platform_admin"], "scope": {"tenant_id": "unit-tenant"}},
                "admin-b": {"roles": ["platform_admin"], "scope": {"tenant_id": "unit-tenant"}},
                "business-user": {"roles": ["operations_manager"], "scope": {"tenant_id": "unit-tenant"}},
            },
        ),
        audit_log=audit,
    )
    monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
    return FlagContext(
        TestClient(_test_app(registry, audit, boundary=boundary)), registry, audit
    )


@pytest.mark.parametrize("method,path,body", ADMIN_REQUESTS)
def test_every_admin_route_requires_authentication(authorized_flags, method, path, body):
    context = authorized_flags
    before = context.registry.all()

    response = context.client.request(method, path, json=body)

    assert response.status_code == 401
    assert context.registry.all() == before


@pytest.mark.parametrize("method,path,body", ADMIN_REQUESTS)
def test_business_principal_cannot_read_or_mutate_admin_flags(
    authorized_flags, method, path, body
):
    context = authorized_flags
    before = context.registry.all()

    response = context.client.request(
        method, path, json=body, headers=_headers("business-user")
    )

    assert response.status_code == 403
    assert context.registry.all() == before


@pytest.mark.parametrize(
    "mode",
    ["unconfigured", "production-deployment", "dev-admin-profile", "auth-mode"],
)
def test_missing_boundary_never_accepts_spoofable_admin_headers(monkeypatch, mode):
    monkeypatch.setattr(dependencies, "default_boundary", lambda: None)
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    for name in ("ODP_RELEASE_PROFILE", "ODP_AUTH_MODE", "ODP_AUTH_OIDC_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    if mode == "unconfigured":
        monkeypatch.delenv("ODP_PRODUCT_MODE")
    elif mode == "production-deployment":
        monkeypatch.setenv("ODP_DEPLOY_ENV", "production")
    elif mode == "dev-admin-profile":
        monkeypatch.setenv("ODP_RELEASE_PROFILE", "dev-admin")
    else:
        monkeypatch.setenv("ODP_AUTH_MODE", "password")
    registry = FeatureFlagRegistry(
        {KEY: FeatureFlag(key=KEY, owner="configuration-owner", high_risk=True)}
    )
    client = TestClient(_test_app(registry, InMemoryAuditLog(), boundary=None))
    before = registry.all()
    headers = {"x-subject-id": "fake-admin", "x-roles": "platform_admin"}

    for method, path, body in ADMIN_REQUESTS:
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 401
        assert registry.all() == before


def test_public_effective_policy_read_is_preserved(authorized_flags):
    context = authorized_flags

    response = context.client.get("/api/v1/feature-flags")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "count": 1,
        "flags": [context.registry.get(KEY).to_dict()],
    }


def test_unknown_signed_subject_and_header_roles_do_not_grant_administration(
    authorized_flags,
):
    context = authorized_flags

    response = context.client.post(
        BASE + "/" + KEY + "/approve", json={}, headers=_headers("undeclared-subject")
    )

    assert response.status_code == 401
    assert context.registry.get(KEY).approved_by == frozenset()


@pytest.mark.parametrize(
    "suffix,body",
    [
        ("/approve", {"approver": "admin-b"}),
        ("/approve", {"approver": "admin-a"}),
        ("/enable", {"approvals": ["admin-a", "admin-b"]}),
    ],
)
def test_body_approval_names_are_not_authenticated_evidence(
    authorized_flags, suffix, body
):
    context = authorized_flags
    before = context.registry.get(KEY)

    response = context.client.post(
        BASE + "/" + KEY + suffix, json={**COMMAND_PROOF, **body}, headers=_headers("admin-a")
    )

    assert response.status_code == 403
    assert context.registry.get(KEY) == before
    assert not any(
        event.event_type.startswith("feature_flag.")
        for event in context.audit.list_events()
    )


def test_two_distinct_verified_principals_control_enable_and_kill_switch(authorized_flags):
    context = authorized_flags
    path = BASE + "/" + KEY
    for _ in range(2):
        approved = context.client.post(
            path + "/approve", json={**COMMAND_PROOF, **({})}, headers=_headers("admin-a")
        )
        assert approved.status_code == 200
        assert approved.json()["flag"]["approved_by"] == ["admin-a"]
    denied = context.client.post(path + "/enable", json={**COMMAND_PROOF, **({})}, headers=_headers("admin-a"))
    assert denied.status_code == 403
    assert context.registry.get(KEY).enabled is False

    second = context.client.post(path + "/approve", json={**COMMAND_PROOF, **({})}, headers=_headers("admin-b"))
    assert second.status_code == 200
    assert second.json()["flag"]["approved_by"] == ["admin-a", "admin-b"]
    enabled = context.client.post(path + "/enable", json={**COMMAND_PROOF, **({})}, headers=_headers("admin-a"))
    assert enabled.status_code == 200
    assert enabled.json()["flag"]["is_active"] is True
    assert context.registry.is_enabled(KEY) is True
    disabled = context.client.post(path + "/disable", headers=_headers("admin-b"), json=COMMAND_PROOF)
    assert disabled.status_code == 200
    assert disabled.json()["flag"]["is_active"] is False
    assert disabled.json()["flag"]["approved_by"] == []
    assert context.registry.is_enabled(KEY) is False
    events = [
        event for event in context.audit.list_events()
        if event.event_type.startswith("feature_flag.")
    ]
    assert [(event.action, event.actor) for event in events] == [
        ("approve", "admin-a"), ("approve", "admin-a"),
        ("approve", "admin-b"), ("enable", "admin-a"), ("disable", "admin-b"),
    ]
    assert all(event.correlation_id == "unit-request" for event in events)


def test_admin_reads_registration_and_audit_use_actual_request_identity(authorized_flags):
    context = authorized_flags
    key = "unit.new-low-risk"
    headers = _headers("admin-a", correlation_id="unit-real-command-cid")
    listed = context.client.get(BASE, headers=headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    created = context.client.post(
        BASE, json={**COMMAND_PROOF, **({"key": key, "owner": "different-configuration-owner"})},
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["flag"]["owner"] == "different-configuration-owner"
    assert context.client.get(BASE + "/" + key, headers=headers).status_code == 200
    enabled = context.client.post(BASE + "/" + key + "/enable", json={**COMMAND_PROOF, **({})}, headers=headers)
    assert enabled.status_code == 200
    events = [
        event for event in context.audit.list_events()
        if event.event_type in {"feature_flag.register", "feature_flag.enable"}
    ]
    assert len(events) == 2
    assert all(event.actor == "admin-a" for event in events)
    assert all(event.correlation_id == "unit-real-command-cid" for event in events)
    assert not any(event.actor == "different-configuration-owner" for event in events)


def test_create_cannot_downgrade_existing_high_risk_or_erase_approvals(authorized_flags):
    context = authorized_flags
    path = BASE + "/" + KEY
    approved = context.client.post(
        path + "/approve", json={**COMMAND_PROOF, **({})}, headers=_headers("admin-a")
    )
    assert approved.status_code == 200
    before = context.registry.get(KEY)
    assert before.high_risk is True
    assert before.approved_by == frozenset({"admin-a"})

    response = context.client.post(
        BASE, json={**COMMAND_PROOF, **({"key": KEY, "owner": "replacement-owner", "high_risk": False})},
        headers=_headers("admin-a"),
    )

    assert response.status_code == 409
    assert context.registry.get(KEY) == before
    denied = context.client.post(path + "/enable", json={**COMMAND_PROOF, **({})}, headers=_headers("admin-a"))
    assert denied.status_code == 403
    assert context.registry.get(KEY).high_risk is True
    assert context.registry.get(KEY).approved_by == frozenset({"admin-a"})
    assert context.registry.get(KEY).enabled is False


def test_main_versioned_and_legacy_admin_mounts_keep_rbac(monkeypatch):
    from apps.api.oday_api.main import create_app
    from shared.auth.feature_flags import default_registry, reset_global_registry

    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    for name in ("ODP_RELEASE_PROFILE", "ODP_AUTH_MODE", "ODP_AUTH_OIDC_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(dependencies, "default_boundary", lambda: None)
    reset_global_registry()
    try:
        client = TestClient(create_app())
        before = default_registry().all()
        business = {"x-subject-id": "unit-business", "x-roles": "operations_manager"}
        for prefix in ("/api/v1", ""):
            assert client.get(prefix + "/admin/feature-flags", headers=business).status_code == 403
            response = client.post(
                prefix + "/admin/feature-flags/high_risk.model.publish/approve",
                json={"approver": "fake-approver"}, headers=business,
            )
            assert response.status_code == 403
            assert default_registry().all() == before
        admin = {"x-subject-id": "unit-admin", "x-roles": "platform_admin"}
        assert client.get("/api/v1/admin/feature-flags", headers=admin).status_code == 200
    finally:
        reset_global_registry()


def test_default_boundary_is_pinned_for_two_router_instances(monkeypatch):
    boundary_a = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer=ISSUER, audiences=frozenset({"unit-feature-api"}),
            signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
            principal_mappings={
                "admin-a": {"roles": ["platform_admin"], "scope": {"tenant_id": "tenant-a"}},
            },
        )
    )
    other_key = SigningKey(
        kid="other-owner", algorithm="HS256", secret=b"other-unit-feature-signing-key"
    )
    other_issuer = "https://other-feature-owner.invalid"
    boundary_b = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer=other_issuer, audiences=frozenset({"unit-feature-api"}),
            signing_keys={other_key.kid: other_key},
            principal_mappings={
                "admin-b": {"roles": ["platform_admin"], "scope": {"tenant_id": "tenant-b"}},
            },
        )
    )
    registry_a = FeatureFlagRegistry(
        {KEY: FeatureFlag(key=KEY, owner="owner-a", high_risk=True)}
    )
    registry_b = FeatureFlagRegistry(
        {KEY: FeatureFlag(key=KEY, owner="owner-b", high_risk=True)}
    )
    audit_a, audit_b = InMemoryAuditLog(), InMemoryAuditLog()
    current_owner = {"boundary": boundary_a}
    # One stable resolver must expose the later owner to an unpinned router.
    monkeypatch.setattr(dependencies, "default_boundary", lambda: current_owner["boundary"])
    client_a = TestClient(_test_app(registry_a, audit_a, boundary=None))
    current_owner["boundary"] = boundary_b
    client_b = TestClient(_test_app(registry_b, audit_b, boundary=None))
    a_headers = _headers("admin-a")
    b_headers = _headers("admin-b", issuer=other_issuer, signing_key=other_key)

    allowed_a = client_a.post(BASE + "/" + KEY + "/approve", json={**COMMAND_PROOF, **({})}, headers=a_headers)
    assert allowed_a.status_code == 200
    assert registry_a.get(KEY).approved_by == frozenset({"admin-a"})
    assert registry_b.get(KEY).approved_by == frozenset()
    assert client_a.post(
        BASE + "/" + KEY + "/approve", json={**COMMAND_PROOF, **({})}, headers=b_headers
    ).status_code == 401
    assert client_b.post(
        BASE + "/" + KEY + "/approve", json={**COMMAND_PROOF, **({})}, headers=a_headers
    ).status_code == 401
    allowed_b = client_b.post(BASE + "/" + KEY + "/approve", json={**COMMAND_PROOF, **({})}, headers=b_headers)
    assert allowed_b.status_code == 200
    assert registry_b.get(KEY).approved_by == frozenset({"admin-b"})
    assert registry_a.get(KEY).approved_by == frozenset({"admin-a"})
    assert [
        event.actor for event in audit_a.list_events()
        if event.event_type == "feature_flag.approve"
    ] == ["admin-a"]
    assert [
        event.actor for event in audit_b.list_events()
        if event.event_type == "feature_flag.approve"
    ] == ["admin-b"]


def test_captured_no_boundary_cannot_mix_new_auth_with_poc_body_names(
    authorized_flags, monkeypatch
):
    actual_boundary = dependencies.default_boundary()
    assert isinstance(actual_boundary, AuthenticationBoundary)
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    for name in ("ODP_RELEASE_PROFILE", "ODP_AUTH_MODE", "ODP_AUTH_OIDC_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    current_owner = {"boundary": None}
    # Keep the resolver callable stable, like the actual default_boundary
    # function whose cached owner is reset when another app is composed.
    monkeypatch.setattr(dependencies, "default_boundary", lambda: current_owner["boundary"])
    registry = FeatureFlagRegistry(
        {KEY: FeatureFlag(key=KEY, owner="poc-owner", high_risk=True)}
    )
    client = TestClient(_test_app(registry, InMemoryAuditLog(), boundary=None))
    local_headers = {"x-subject-id": "unit-poc-admin", "x-roles": "platform_admin"}
    assert client.get(BASE, headers=local_headers).status_code == 200
    current_owner["boundary"] = actual_boundary
    before = registry.all()

    # The old instance must be rebuilt with its intended owner, not adopt
    # another instance's boundary while retaining local body-name authority.
    for headers in (local_headers, _headers("admin-a")):
        denied = client.post(
            BASE + "/" + KEY + "/approve",
            json={"approver": "arbitrary-body-name"}, headers=headers,
        )
        assert denied.status_code == 401
        assert registry.all() == before
        assert client.get(BASE, headers=headers).status_code == 401

