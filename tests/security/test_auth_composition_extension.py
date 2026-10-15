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
    assert resolver.resolve("known") is None  # Failed attempt cannot be retried inside cooldown.
    ticks[0] = 31.0
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


@pytest.mark.parametrize("prefix", ["", "/api/v1"])
def test_future_domain_audit_uses_verified_tenant_and_keeps_history_hidden(
    monkeypatch: Any, prefix: str
) -> None:
    """Mounted JWT producers, not a read-filter waiver or historical rewrite."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    from modules.forecastops import default_forecast_alert_policy
    from shared.governance import InMemoryDecisionPolicyRepository

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://offline.invalid/jwks",
            principal_mappings={
                "operator-real-1": {
                    "roles": ["operations_manager", "regional_supervisor", "expansion_user", "auditor"],
                    "scope": {"tenant_id": TENANT},
                },
                "foreign-auditor": {"roles": ["auditor"], "scope": {"tenant_id": OTHER}},
            },
        ),
        key_resolver=JwksResolver(
            "https://offline.invalid/jwks", fetch=lambda _: {"keys": [_jwk(key, "offline-key")]}
        ),
    )
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    correlation = "same-domain-producer-selector"
    headers = {
        "authorization": "Bearer " + _token(key, "offline-key"),
        "x-correlation-id": correlation,
    }
    foreign = {"authorization": "Bearer " + _token(key, "offline-key", sub="foreign-auditor")}
    audit = InMemoryAuditLog()
    historical = audit.record(AuditEvent(
        event_type="offline.tenantless-history", actor="fixture", action="offline",
        resource="offline", outcome="retained", correlation_id=correlation,
        metadata={"marker": "TENANTLESS-HISTORY-MUST-STAY-HIDDEN"},
    ))
    frozen = historical.to_dict()
    policies = InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT)])
    with TestClient(create_app(audit_log=audit, forecastops_policy_repository=policies)) as client:
        forecast = client.post(prefix + "/forecastops/forecast-jobs", headers=headers, json={
            "prediction_origin_time": "2026-06-27T09:00:00+00:00",
            "inputs": [{"store_id": "offline-store", "observations": [
                {"business_date": "2026-06-25", "actual_revenue": 120000, "site_score_baseline_p50": 120000},
                {"business_date": "2026-06-26", "actual_revenue": 90000, "site_score_baseline_p50": 120000},
            ]}],
        })
        assert forecast.status_code == 202, forecast.text
        body = forecast.json()
        alert_id = body["alerts"][0]["alert_id"]
        handoff_id = body["handoffs"][0]["handoff_id"]
        ack = client.post(prefix + f"/forecastops/alerts/{alert_id}/acknowledge", headers=headers,
                          json={"actor": "offline-operator", "note": "offline-triage"})
        assert ack.status_code == 200, ack.text
        handoff = client.post(prefix + f"/forecastops/intervention-handoffs/{handoff_id}/execute",
                              headers=headers, json={"actor": "offline-operator", "intervention_id": "offline-intervention"})
        assert handoff.status_code == 200, handoff.text
        feedback = client.post(prefix + "/forecastops/feedbacks", headers=headers, json={
            "store_id": "offline-store", "feedback_type": "context_annotation",
            "reason": "offline-context", "actor": "offline-operator",
            "target_date_start": "2026-06-25", "target_date_end": "2026-06-26",
            "metadata": {"tenant_id": OTHER},
        })
        assert feedback.status_code == 201, feedback.text
        scored = client.post(prefix + "/heatzones/score-jobs", headers=headers, json={
            "features": [{"h3_index": "h3r9_0200_0200", "poi_count": 15, "competitor_count": 1,
                          "active_listing_count": 4, "median_listing_rent": 50000,
                          "average_confidence": 0.9, "source_snapshot_ids": ["offline-source"], "tenant_id": OTHER}],
            "idempotency_key": "offline-heatzone-replay",
        })
        assert scored.status_code == 202, scored.text
        replay = client.post(prefix + "/heatzones/score-jobs", headers=headers,
                             json={"features": [], "idempotency_key": "offline-heatzone-replay"})
        assert replay.status_code == 202 and replay.json()["created"] is False, replay.text
        expected_ids = {r.json()["audit_event_id"] for r in (ack, handoff, feedback, scored, replay)}
        own = client.get(prefix + "/audit/events", headers=headers, params={"correlation_id": correlation})
        assert own.status_code == 200, own.text
        own_events = {event["event_id"]: event for event in own.json()["events"]}
        assert expected_ids <= own_events.keys()
        assert all(own_events[event_id]["metadata"]["tenant_id"] == TENANT for event_id in expected_ids)
        for denied_headers in ({}, foreign):
            denied = client.get(prefix + "/audit/events", headers=denied_headers,
                                params={"correlation_id": correlation})
            if denied_headers:
                assert denied.status_code == 200, denied.text
                assert not (expected_ids & {event["event_id"] for event in denied.json()["events"]})
            else:
                assert denied.status_code in (401, 403)
            assert "offline-triage" not in denied.text
        assert "TENANTLESS-HISTORY-MUST-STAY-HIDDEN" not in own.text
        assert historical.to_dict() == frozen


def test_product_seed_uses_supported_authorized_queue_and_real_fixture_inputs(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """Exercise the actual seed HTTP helper against durable mounted routes.

    This local transport control is not a substitute for the complete Docker
    E2E inventory or a scheduler-execution receipt.
    """
    import io
    from urllib.error import HTTPError
    from urllib.parse import urlsplit

    from delivery_toolchain.e2e import seed_product_e2e_data as seed
    from shared.infrastructure.persistence.factory import _durable_bundle

    bundle = _durable_bundle(str(tmp_path / "offline-seed.sqlite3"))
    with TestClient(create_app(persistence=bundle)) as client:
        def local_http(request: Any, timeout: float) -> Any:
            path = urlsplit(request.full_url).path
            response = client.request(request.get_method(), path,
                                      headers=dict(request.header_items()), content=request.data)
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, response.text, {}, None)
            return io.BytesIO(response.content)

        monkeypatch.setattr(seed, "urlopen", local_http)
        first = seed.seed_supported_queue_job("http://offline.invalid", case_id="offline-case")
        replay = seed.seed_supported_queue_job("http://offline.invalid", case_id="offline-case")
        assert first["job_id"] == replay["job_id"] and replay["created"] is False
        record = seed.get_json("http://offline.invalid/jobs/" + first["job_id"])
        assert record["job_type"] == "forecast"
        assert record["payload"]["tenant_id"] == seed.TENANT_ID
        series = bundle.forecastops_repository.get_series(seed.TENANT_ID, record["payload"]["store_id"])
        assert series is not None and len(series.observations) == 2
        own = seed.get_json("http://offline.invalid/audit/events")
        assert any(event["event_id"] == first["audit_event_id"] for event in own["events"])
        with pytest.raises(HTTPError) as denied:
            seed.post_json("http://offline.invalid/jobs", {
                "job_type": "product-e2e.scheduler.heartbeat", "payload": {"tenant_id": seed.TENANT_ID},
            })
        assert denied.value.code == 403
        anonymous = client.get("/jobs/" + first["job_id"])
        assert anonymous.status_code in (401, 403)
        foreign = client.get("/jobs/" + first["job_id"], headers={
            "x-subject-id": "foreign-fixture", "x-roles": "operations_manager", "x-tenant-id": "foreign-tenant",
        })
        assert foreign.status_code == 403
    bundle.engine.close()
