from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app


class DummyProviderValidationResult:
    def __init__(
        self,
        ok: bool,
        errors: tuple[str, ...] = (),
        *,
        mode: str = "fixture",
    ) -> None:
        self.ok = ok
        self.errors = errors
        self.mode = SimpleNamespace(value=mode)


def _connectivity_result(
    *,
    healthy: bool,
    reason_code: str = "ok",
) -> dict[str, Any]:
    checked_at = datetime.now(UTC)
    expires_at = checked_at + timedelta(seconds=60)
    providers = (
        "listing.partner_feed",
        "poi.commercial_api",
        "geocode.primary_api",
        "admin_boundary.official_dataset",
    )
    return {
        "mode": "live",
        "configuration_valid": True,
        "connectivity_healthy": healthy,
        "checked_at": checked_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "required_provider_ids": list(providers),
        "probes": [
            {
                "provider_id": provider_id,
                "configuration_valid": True,
                "connectivity_healthy": healthy,
                "authentication_accepted": healthy,
                "response_valid": healthy,
                "schema_valid": healthy,
                "checked_at": checked_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "latency_ms": 3,
                "http_status": 200 if healthy else 401,
                "reason_code": reason_code,
            }
            for provider_id in providers
        ],
    }


def test_healthz_liveness() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "oday-api"}


def test_readiness_healthy() -> None:
    client = TestClient(create_app())
    response = client.get("/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "database" in body["details"]


def test_health_detailed_healthy() -> None:
    client = TestClient(create_app())
    response = client.get("/health", headers={"x-correlation-id": "corr-test-99"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "oday-api"
    assert body["version"] == "0.1.0"
    assert body["correlation_id"] == "corr-test-99"
    assert "time" in body
    assert body["dependencies"]["database"] == "healthy (in-memory)"
    assert body["dependencies"]["job_queue"] == "healthy (in-memory job queue)"
    providers = body["dependencies"]["external_providers"]
    assert providers["status"] == "healthy"
    assert providers["configuration_valid"] is True
    assert providers["connectivity_healthy"] is None


def test_health_unhealthy_when_provider_fails() -> None:
    # Force unhealthy provider validation
    bad_validation = DummyProviderValidationResult(ok=False, errors=("License expired",))
    app = create_app(external_provider_validation=bad_validation)
    client = TestClient(app)

    response = client.get("/health", headers={"x-correlation-id": "corr-test-fail"})
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    providers = body["dependencies"]["external_providers"]
    assert providers["status"] == "unhealthy"
    assert providers["configuration_valid"] is False


def test_live_provider_health_requires_connectivity_evidence() -> None:
    validation = DummyProviderValidationResult(ok=True, mode="live")
    probe_calls = 0

    def probe(**_kwargs):
        nonlocal probe_calls
        probe_calls += 1
        return _connectivity_result(
            healthy=False,
            reason_code="unauthorized",
        )

    app = create_app(
        external_provider_validation=validation,
        external_provider_connectivity_probe=probe,
    )
    client = TestClient(app)
    response = client.get(
        "/platform/health",
        headers={"x-correlation-id": "corr-provider-health"},
    )
    cached_response = client.get(
        "/platform/health",
        headers={"x-correlation-id": "corr-provider-health-cached"},
    )

    assert response.status_code == 503
    assert cached_response.status_code == 503
    assert probe_calls == 1
    providers = response.json()["dependencies"]["external_providers"]
    assert providers["configuration_valid"] is True
    assert providers["connectivity_healthy"] is False
    assert {probe["reason_code"] for probe in providers["probes"]} == {"unauthorized"}
    assert "credential" not in response.text.lower()


def test_live_provider_health_exposes_successful_probe_evidence() -> None:
    validation = DummyProviderValidationResult(ok=True, mode="live")
    app = create_app(
        external_provider_validation=validation,
        external_provider_connectivity_probe=(lambda **_kwargs: _connectivity_result(healthy=True)),
    )
    response = TestClient(app).get(
        "/platform/health",
        headers={"x-correlation-id": "corr-provider-health-ok"},
    )

    assert response.status_code == 200
    providers = response.json()["dependencies"]["external_providers"]
    assert providers["configuration_valid"] is True
    assert providers["connectivity_healthy"] is True
    assert len(providers["probes"]) == 4
    assert all(
        probe["authentication_accepted"] and probe["response_valid"] and probe["schema_valid"]
        for probe in providers["probes"]
    )
