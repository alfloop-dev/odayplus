"""Actual dependency/JWT/mounted API composition, synthetic offline authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.api.oday_api.runtime_mode import live_data_required
from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    AuthFailureReason,
    Credentials,
    JwksResolver,
)
from shared.audit import AuditEvent, InMemoryAuditLog
from tests.security.test_opsboard_jwks_auth import _jwk, _token

MODE_KEYS = (
    "ODP_DEPLOY_ENV",
    "ODAY_ENV",
    "ODP_ENV",
    "APP_ENV",
    "ENVIRONMENT",
    "ODP_PRODUCT_MODE",
    "NODE_ENV",
    "ODP_REQUIRE_LIVE_DATA",
)
TENANT = "10000000-0000-4000-8000-000000000001"
OTHER = "20000000-0000-4000-8000-000000000002"


@pytest.fixture(autouse=True)
def isolation(monkeypatch: Any) -> Any:
    import os

    for key in tuple(os.environ):
        if key in MODE_KEYS or key.startswith("ODP_AUTH_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NODE_ENV", "test")
    deps.reset_bound_persistence()
    yield
    deps.reset_bound_persistence()


@pytest.mark.parametrize(
    "key,value",
    [
        ("ODP_DEPLOY_ENV", "production"),
        ("ODAY_ENV", "staging"),
        ("ODP_ENV", "prod"),
        ("APP_ENV", "stage"),
        ("ENVIRONMENT", "live"),
        ("NODE_ENV", "production"),
        ("ODP_REQUIRE_LIVE_DATA", "true"),
        ("ODP_PRODUCT_MODE", "STAGING"),
    ],
)
def test_all_authoritative_live_modes_refuse_spoofed_header_admin(
    key: str, value: str, monkeypatch: Any
) -> None:
    monkeypatch.setenv(key, value)
    assert live_data_required()
    # Auth inputs absent; actual default boundary has no credentials configured.
    assert deps.default_boundary() is None
    with pytest.raises(HTTPException) as caught:
        deps.principal_from_headers(
            {"x-subject-id": "forged", "x-roles": "platform_admin", "x-tenant-id": TENANT}
        )
    assert caught.value.status_code == 401


@pytest.mark.parametrize("refresh", ["outage", "empty", "malformed"])
def test_expired_jwks_cannot_authenticate_using_retained_signing_authority(refresh: str) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ticks = [0.0]
    replies: list[Any] = [
        {"keys": [_jwk(key, "offline-key")]},
        {"keys": []} if refresh == "empty" else {},
    ]
    calls = [0]

    def fetch(_: str) -> dict[str, Any]:
        calls[0] += 1
        if calls[0] > 1 and refresh == "outage":
            raise ValueError("PRIVATE-PROVIDER-ERROR")
        return replies[min(calls[0] - 1, 1)]

    resolver = JwksResolver(
        "https://offline.invalid/jwks", cache_ttl_seconds=30, fetch=fetch, clock=lambda: ticks[0]
    )
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://offline.invalid/jwks",
            principal_mappings={
                "operator-real-1": {"roles": ["auditor"], "scope": {"tenant_id": TENANT}}
            },
        ),
        key_resolver=resolver,
    )
    credentials = Credentials(bearer_token=_token(key, "offline-key"))
    assert boundary.authenticate(credentials).authenticated
    ticks[0] = 30.0  # exact expiry, no timing sleep
    outcome = boundary.authenticate(credentials)
    assert not outcome.authenticated
    assert outcome.reason is AuthFailureReason.UNKNOWN_KEY
    assert calls[0] == 2
    ticks[0] = 3000.0
    assert not boundary.authenticate(credentials).authenticated


def test_jwks_outage_retains_only_unexpired_cache_and_healthy_refresh_recovers() -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ticks = [0.0]
    healthy = [True]

    def fetch(_: str) -> dict[str, Any]:
        if not healthy[0]:
            raise ValueError("OFFLINE-UNAVAILABLE")
        return {"keys": [_jwk(key, "known")]}

    resolver = JwksResolver(
        "https://offline.invalid/jwks", cache_ttl_seconds=30, fetch=fetch, clock=lambda: ticks[0]
    )
    first = resolver.resolve("known")
    assert first is not None
    healthy[0] = False
    ticks[0] = 29.0
    assert resolver.resolve("missing") is None  # actual failed refresh, not unused fixture response
    assert resolver.resolve("known") is first
    ticks[0] = 30.0
    assert resolver.resolve("known") is None
    healthy[0] = True
    assert resolver.resolve("known") is not None


@pytest.fixture
def audit_client() -> Any:
    audit = InMemoryAuditLog()
    for tenant, marker in [(TENANT, "own-event"), (OTHER, "OTHER-TENANT-PRIVATE-MARKER")]:
        audit.record(
            AuditEvent(
                event_type="test.offline",
                actor="fixture",
                action="VIEW",
                resource="audit",
                outcome="success",
                correlation_id="same-caller-selector",
                metadata={"tenant_id": tenant, "marker": marker},
                occurred_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    client = TestClient(create_app(audit_log=audit))
    yield client, audit
    client.close()


@pytest.mark.parametrize("path", ["/audit/events", "/api/v1/audit/events"])
@pytest.mark.parametrize(
    "headers", [{}, {"x-subject-id": "fixture", "x-roles": "platform_admin", "x-tenant-id": TENANT}]
)
def test_generic_audit_both_aliases_require_audit_read_authority(
    audit_client: Any, path: str, headers: dict[str, str]
) -> None:
    response = audit_client[0].get(path, headers=headers)
    assert response.status_code in (401, 403)
    assert "OTHER-TENANT-PRIVATE-MARKER" not in response.text


@pytest.mark.parametrize("path", ["/audit/events", "/api/v1/audit/events"])
@pytest.mark.parametrize("selector", [None, "same-caller-selector"])
def test_generic_audit_authorized_read_never_selects_foreign_tenant(
    audit_client: Any, path: str, selector: str | None
) -> None:
    response = audit_client[0].get(
        path,
        headers={"x-subject-id": "fixture", "x-roles": "auditor", "x-tenant-id": TENANT},
        params={"correlation_id": selector} if selector else None,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["events"]) == 1
    assert "OTHER-TENANT-PRIVATE-MARKER" not in response.text


@pytest.mark.parametrize("path", ["/audit/events", "/api/v1/audit/events"])
def test_generic_correlation_filter_cannot_hide_malformed_fixed_history(
    audit_client: Any, path: str
) -> None:
    from shared.identity.dev_smoke_journal import TENANT_ID as PINNED_TENANT
    from shared.identity.dev_smoke_journal import ProvisioningJournal

    client, audit = audit_client
    audit.record(
        AuditEvent(
            event_type=ProvisioningJournal._TYPE,
            actor="fixture",
            action="offline",
            resource="offline",
            outcome="success",
            correlation_id=ProvisioningJournal._CORRELATION,
            metadata={"tenant_id": OTHER, "PRIVATE-EXTENSION": "DO-NOT-ECHO"},
        )
    )
    response = client.get(
        path,
        headers={"x-subject-id": "fixture", "x-roles": "auditor", "x-tenant-id": PINNED_TENANT},
        params={"correlation_id": "unrelated-caller-selector"},
    )
    assert response.status_code == 403
    assert "DO-NOT-ECHO" not in response.text
