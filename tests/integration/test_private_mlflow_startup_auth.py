"""Exercise the first real MLflow SDK request during API composition.

The HTTP service and token are explicit local doubles. This checks ordering and
header construction; it does not certify a deployed workload or real models.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from apps.api.oday_api.main import create_app
from modules.learninghub.infrastructure.mlflow_cloud_run_auth import (
    CLOUD_RUN_MLFLOW_AUTH_NAME,
    CloudRunIdTokenAuth,
)
from tests.integration.test_production_api_composition import (
    _live_provider,
    _production_backed_bundle,
)


def test_first_model_lookup_uses_identity_auth_before_learninghub_composes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    from mlflow.tracking.request_auth.registry import _request_auth_provider_registry

    origin = "https://synthetic-private-mlflow-123.asia-east1.run.app"
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", origin)
    monkeypatch.setenv("ODP_MLFLOW_CLOUD_RUN_AUDIENCE", origin)
    for name in ["MLFLOW_TRACKING_AUTH", "MLFLOW_TRACKING_TOKEN", "MLFLOW_TRACKING_USERNAME", "MLFLOW_TRACKING_PASSWORD"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_request_auth_provider_registry, "_registry", [
        provider for provider in _request_auth_provider_registry
        if provider.get_name() != CLOUD_RUN_MLFLOW_AUTH_NAME
    ])
    monkeypatch.setattr(CloudRunIdTokenAuth, "_current_token", lambda self: "synthetic-test-identity-token")
    requests_seen: list[dict[str, Any]] = []

    def private_registry(_session: Any, request: requests.PreparedRequest, **_kwargs: Any) -> requests.Response:
        assert request.url and request.url.startswith(origin + "/")
        authenticated = request.headers.get("Authorization") == "Bearer synthetic-test-identity-token"
        requests_seen.append({"url": request.url, "authenticated": authenticated})
        response = requests.Response()
        response.status_code = 404 if authenticated else 403
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({
            "error_code": "RESOURCE_DOES_NOT_EXIST" if authenticated else "PERMISSION_DENIED",
            "message": "synthetic registry intentionally has no production model" if authenticated else "synthetic private service requires identity",
        }).encode()
        return response

    monkeypatch.setattr(requests.Session, "send", private_registry)
    bundle = _production_backed_bundle(tmp_path / "synthetic-private-registry-composition.sqlite3")
    try:
        app = create_app(persistence=bundle, external_provider_validation=_live_provider())
    finally:
        bundle.engine.close()
    assert requests_seen, "The API must attempt a real SDK model lookup"
    assert requests_seen[0]["authenticated"], requests_seen
    assert all(item["authenticated"] for item in requests_seen), requests_seen
    assert app.state.production_model_capabilities["forecastops"]["available"] is False
    assert app.state.production_model_capabilities["forecastops"]["reasonCode"] == "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"


@pytest.mark.parametrize("invalid_configuration", ["missing-audience", "other-service", "other-auth-provider", "insecure-origin", "malformed-origin"])
def test_invalid_identity_configuration_stops_before_any_model_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, invalid_configuration: str,
) -> None:
    origin = "https://synthetic-private-mlflow-123.asia-east1.run.app"
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", origin)
    monkeypatch.setenv("ODP_MLFLOW_CLOUD_RUN_AUDIENCE", origin)
    monkeypatch.delenv("MLFLOW_TRACKING_AUTH", raising=False)
    if invalid_configuration == "missing-audience":
        monkeypatch.delenv("ODP_MLFLOW_CLOUD_RUN_AUDIENCE")
    elif invalid_configuration == "other-service":
        monkeypatch.setenv("ODP_MLFLOW_CLOUD_RUN_AUDIENCE", "https://synthetic-other-456.asia-east1.run.app")
    elif invalid_configuration == "other-auth-provider":
        monkeypatch.setenv("MLFLOW_TRACKING_AUTH", "synthetic-other-provider")
    elif invalid_configuration == "insecure-origin":
        monkeypatch.setenv("MLFLOW_TRACKING_URI", origin.replace("https:", "http:"))
    elif invalid_configuration == "malformed-origin":
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://[broken.run.app")
    seen: list[str | None] = []

    def no_model_request(_session: Any, request: requests.PreparedRequest, **_kwargs: Any) -> requests.Response:
        seen.append(request.url)
        raise AssertionError("Invalid identity configuration must stop before model HTTP")

    monkeypatch.setattr(requests.Session, "send", no_model_request)
    bundle = _production_backed_bundle(tmp_path / "synthetic-invalid-registry-config.sqlite3")
    try:
        app = create_app(persistence=bundle, external_provider_validation=_live_provider())
    finally:
        bundle.engine.close()
    assert seen == []
    assert app.state.production_model_capabilities["forecastops"]["available"] is False
    assert app.state.production_model_capabilities["forecastops"]["reasonCode"] == "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"
