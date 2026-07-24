from __future__ import annotations

from typing import Any

import pytest
import uvicorn
from fastapi import Request, Response

from apps.api import server
from apps.api.oday_api.main import create_app
from models import shared_ml
from modules.opsboard.application import operator_state
from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle


def route_for(app: Any, target_path: str) -> Any:
    def walk(router: Any, prefix: str = "") -> Any:
        for route in getattr(router, "routes", []):
            path = getattr(route, "path", "")
            if path and f"{prefix}{path}" == target_path:
                return route
            nested = getattr(route, "original_router", None)
            if nested is None:
                continue
            context = getattr(route, "include_context", None)
            nested_prefix = getattr(context, "prefix", "") or ""
            matched = walk(nested, f"{prefix}{nested_prefix}")
            if matched is not None:
                return matched
        return None

    route = walk(app)
    assert route is not None, f"route not found: {target_path}"
    return route


def request_context(path: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "root_path": "",
            "http_version": "1.1",
        }
    )
    request.state.correlation_id = "corr-live-gate"
    return request


def readiness_payload(app: Any) -> tuple[int, dict[str, Any]]:
    response = Response()
    body = route_for(app, "/readiness").endpoint(response)
    return response.status_code, body


def test_live_required_operator_state_never_loads_seed(monkeypatch: Any) -> None:
    def fail_if_loaded() -> dict[str, Any]:
        raise AssertionError("load_r4_seed must not run when live data is required")

    monkeypatch.setattr(operator_state, "load_r4_seed", fail_if_loaded)

    service = operator_state.OperatorStateService(
        require_live_data=True,
        persistence_mode="memory",
        provider_mode="fixture",
    )
    envelope = service.get_today(role_id="ops-lead")

    assert envelope["workQueue"] == []
    assert envelope["approvals"] == []
    assert envelope["kpis"] == []
    assert envelope["meta"]["dataMode"] == "unavailable"
    assert envelope["meta"]["dataOrigin"] == {
        "kind": "unavailable",
        "sourceId": None,
        "persistenceMode": "memory",
        "providerMode": "fixture",
    }
    assert envelope["meta"]["liveReadiness"] == {
        "required": True,
        "ready": False,
        "reasonCode": "OPERATOR_LIVE_REPOSITORY_UNAVAILABLE",
    }


def test_explicit_live_gate_fails_closed_for_memory_and_fixtures(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("ODP_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ODP_PERSISTENCE", raising=False)
    app = create_app(persistence=_memory_bundle())

    readiness_status, readiness_body = readiness_payload(app)
    assert readiness_status == 503
    readiness_details = readiness_body["details"]
    assert readiness_details["requireLiveData"] is True
    assert readiness_details["persistence"] == {
        "configuredMode": "memory",
        "runtimeMode": "memory",
        "durable": False,
        "reachable": True,
        "production_persistence_supported": False,
    }
    assert readiness_details["provider"]["mode"] == "fixture"
    assert readiness_details["provider"]["healthy"] is False
    assert readiness_details["provider"]["live"] is False
    assert "healthy" not in readiness_details["database"]
    assert "healthy" not in readiness_details["external_providers"]
    assert readiness_details["data"]["mode"] == "unavailable"
    assert readiness_details["data"]["liveReady"] is False
    assert set(readiness_details["data"]["blockingReasons"]) == {
        "MEMORY_PERSISTENCE",
        "PROVIDER_NOT_LIVE",
        "OPERATOR_LIVE_REPOSITORY_UNAVAILABLE",
        "PRODUCTION_MODEL_BINDINGS_UNVERIFIED",
    }

    health_response = Response()
    health_body = route_for(app, "/platform/health").endpoint(
        request_context("/platform/health"),
        health_response,
    )
    assert health_response.status_code == 503
    assert health_body["modes"]["data"]["liveReady"] is False

    bootstrap = route_for(
        app,
        "/api/v1/operator/bootstrap",
    ).endpoint(
        request_context("/api/v1/operator/bootstrap"),
        x_operator_role="ops-lead",
        x_subject_id="test-ops-manager",
        x_roles="operations_manager",
        x_correlation_id="corr-live-gate",
    )
    assert bootstrap["workQueue"] == []
    assert bootstrap["meta"]["dataOrigin"]["kind"] == "unavailable"

    with pytest.raises(AssertionError, match="route not found"):
        route_for(app, "/api/v1/operator/seed/reset")
    assert "/api/v1/operator/seed/reset" not in app.openapi()["paths"]


def test_live_gate_does_not_seed_or_promote_baseline_models(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")

    def fail_if_seeded(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("live-required startup must not seed baseline models")

    monkeypatch.setattr(shared_ml, "seed_scoring_models", fail_if_seeded)

    app = create_app(persistence=_memory_bundle())
    response_status, response_body = readiness_payload(app)

    assert response_status == 503
    models = response_body["details"]["models"]
    assert models["mode"] == "mlflow-production-unverified"
    assert models["productionBindingsReady"] is False
    assert models["autoSeeded"] is False
    assert "MLFLOW_TRACKING_URI" in models["error"]
    assert (
        "PRODUCTION_MODEL_BINDINGS_UNVERIFIED"
        in response_body["details"]["data"]["blockingReasons"]
    )
    assert app.state.scoring_bindings == {}
    assert app.state.model_runtime is None


def test_live_gate_rejects_durable_sqlite_as_production_persistence(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("ODP_PERSISTENCE", raising=False)
    bundle = _durable_bundle(tmp_path / "local-e2e.sqlite3")

    response_status, response_body = readiness_payload(
        create_app(persistence=bundle)
    )

    assert response_status == 503
    persistence = response_body["details"]["persistence"]
    assert persistence["runtimeMode"] == "durable"
    assert persistence["durable"] is True
    assert persistence["production_persistence_supported"] is False
    assert (
        "SQLITE_NOT_PRODUCTION_PERSISTENCE"
        in response_body["details"]["data"]["blockingReasons"]
    )


def test_postgres_mode_requires_database_url_without_falling_back(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgres")
    monkeypatch.delenv("ODAY_DATABASE_URL", raising=False)

    with pytest.raises(
        RuntimeError,
        match="ODAY_DATABASE_URL is required for PostgreSQL persistence",
    ):
        create_app()


def test_production_deployment_implies_live_data_gate(monkeypatch: Any) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    monkeypatch.setenv("ODP_DEPLOY_ENV", "production")

    app = create_app(persistence=_memory_bundle())
    assert app.state.require_live_data is True
    with pytest.raises(AssertionError, match="route not found"):
        route_for(app, "/api/v1/operator/seed/reset")


def test_local_runtime_keeps_fixture_and_seed_reset(monkeypatch: Any) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    monkeypatch.delenv("ODP_DEPLOY_ENV", raising=False)
    monkeypatch.setenv("APP_ENV", "test")

    service = operator_state.OperatorStateService()
    bootstrap = service.get_today(role_id="ops-lead")
    assert bootstrap["meta"]["dataMode"] == "fixture"
    assert bootstrap["meta"]["dataOrigin"]["sourceId"] == "r4-seed"
    assert bootstrap["kpis"]
    service.reset_to_seed()

    app = create_app()
    readiness_status, _ = readiness_payload(app)
    assert readiness_status == 200


def test_server_main_runs_the_composed_app_instance(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("ODP_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    captured: dict[str, Any] = {}
    application = create_app(persistence=_memory_bundle())

    def capture_run(application: Any, **kwargs: Any) -> None:
        captured["application"] = application
        captured["kwargs"] = kwargs

    monkeypatch.setattr(server, "build_server", lambda: application)
    monkeypatch.setattr(uvicorn, "run", capture_run)
    server.main()

    started_application = captured["application"]
    assert started_application is application
    assert not isinstance(started_application, str)
    assert started_application.state.require_live_data is True
    readiness_status, _ = readiness_payload(started_application)
    assert readiness_status == 503
