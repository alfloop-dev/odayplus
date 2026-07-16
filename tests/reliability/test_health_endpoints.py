from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app


class DummyProviderValidationResult:
    def __init__(self, ok: bool, errors: tuple[str, ...] = ()) -> None:
        self.ok = ok
        self.errors = errors


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
    assert body["dependencies"]["job_queue"] == "healthy"
    assert body["dependencies"]["external_providers"] == "healthy"


def test_health_unhealthy_when_provider_fails() -> None:
    # Force unhealthy provider validation
    bad_validation = DummyProviderValidationResult(ok=False, errors=("License expired",))
    app = create_app(external_provider_validation=bad_validation)
    client = TestClient(app)

    response = client.get("/health", headers={"x-correlation-id": "corr-test-fail"})
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert "unhealthy" in body["dependencies"]["external_providers"]
