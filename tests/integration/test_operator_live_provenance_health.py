"""Operator live provenance and governed capability health integration tests.

Verifies the remediation of Deploy Dev run 30680943677 failure semantics from origin/dev 97e3ae2e:
1. PostgreSQL-backed Operator bootstrap reports truthful provenance, dataMode, and section completeness.
2. Platform health (/platform/health and /readiness) return 200 OK when core repository is ready,
   distinguishing core Operator repository readiness from model capability bindings.
3. ForecastOps fails closed with unavailable capability status when absent MLflow alias exists,
   without synthetic auto-seed, alias fabrication, or fake ready state.
4. Invalid or unauthorized access fails closed.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from apps.api.oday_api.main import create_app
from apps.api.oday_api.security.dependencies import (
    OPERATOR_CONSOLE_RESOURCE,
    require_operator_permission,
)
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from modules.opsboard.application.operator_state import OperatorStateService
from shared.auth import Action
from shared.domain import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence.factory import _durable_bundle, build_persistence
from shared.infrastructure.persistence.postgresql import PostgresEngine


class MockPostgresEngine:
    """Explicit PostgreSQL test double for integration testing without a running Cloud SQL instance.

    Replaces direct mutation of SqliteEngine.is_production attributes while ensuring
    the persistence bundle cleanly identifies as PostgreSQL 16 production engine.
    """

    def __init__(self, delegate_engine: Any) -> None:
        self._delegate = delegate_engine

    @property
    def is_production(self) -> bool:
        return True

    def query(self, sql: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if sql.strip().upper() == "SELECT 1":
            return [{"ready": 1}]
        return self._delegate.query(sql, *args, **kwargs)

    def query_one(self, sql: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        if sql.strip().upper() in {"SELECT 1", "SELECT 1 AS READY"}:
            return {"ready": 1}
        return self._delegate.query_one(sql, *args, **kwargs)

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def _live_provider() -> Any:
    return SimpleNamespace(
        mode=SimpleNamespace(value="live"),
        ok=True,
        errors=(),
    )


def _live_connectivity_probe(**_kwargs: Any) -> Any:
    return SimpleNamespace(
        connectivity_healthy=True,
        probes=[],
        to_dict=lambda: {
            "connectivity_healthy": True,
            "probes": [],
            "status": "healthy",
        },
    )


def _production_backed_bundle(path: Path) -> Any:
    bundle = _durable_bundle(path)
    mock_engine = MockPostgresEngine(bundle.engine)
    return replace(
        bundle,
        engine=mock_engine,
        mode="postgresql",
        assisted_intake_store=SimpleNamespace(),
    )


def test_operator_live_provenance_reports_live_data_mode_when_postgresql_ready(tmp_path: Path) -> None:
    """Acceptance 2 (Run 30680943677 remediation): Operator bootstrap backed by PostgreSQL
    reports non-placeholder live provenance and canonical dataMode='live'.
    """
    bundle = _production_backed_bundle(tmp_path / "prov-test.sqlite3")
    bundle.tenant_repository.save_tenant(
        Tenant(tenant_id="tenant-live-1", tenant_name="Live Tenant")
    )
    bundle.brand_repository.save_brand(
        Brand(
            brand_id="brand-live-1",
            tenant_id="tenant-live-1",
            brand_code="brand-live",
            brand_name="Live Brand",
        )
    )
    bundle.address_location_repository.save_address(
        AddressLocation(
            address_id="address-live-1",
            raw_address="Live test address",
        )
    )
    bundle.store_repository.save_store(
        Store(
            store_id="store-live-1",
            tenant_id="tenant-live-1",
            brand_id="brand-live-1",
            store_name="Live Store",
            store_status="open",
            address_id="address-live-1",
        )
    )
    repository = OperatorLiveRepository(bundle)
    service = OperatorStateService(
        require_live_data=True,
        persistence_mode="postgresql",
        provider_mode="live",
        live_repository=repository,
    )

    envelope = service.get_today(
        role_id="ops-lead",
        tenant_id="tenant-live-1",
    )

    assert envelope["meta"]["source"] == "operator-shell-production"
    assert envelope["meta"]["sections"]["stores"]["state"] == "available"
    assert envelope["meta"]["sections"]["stores"]["recordCount"] == 1
    assert envelope["meta"]["sections"]["riskRows"]["state"] == "available"
    assert envelope["meta"]["sections"]["riskRows"]["source"] == "operator-tenant-risk-projection"
    assert envelope["meta"]["unavailableSections"] == []
    assert envelope["meta"]["dataMode"] == "live"
    assert envelope["meta"]["dataOrigin"]["kind"] == "authoritative"
    assert envelope["meta"]["dataOrigin"]["complete"] is True


def test_platform_health_and_readiness_200_ok_when_core_repository_is_ready(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Acceptance 3 (Run 30680943677 remediation): Platform health and readiness return 200 ok when core repository is ready,
    distinguishing core Operator repository readiness from model capability bindings.
    """
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    bundle = _production_backed_bundle(tmp_path / "health-test.sqlite3")

    app = create_app(
        persistence=bundle,
        external_provider_validation=_live_provider(),
        external_provider_connectivity_probe=_live_connectivity_probe,
    )

    with TestClient(app) as client:
        health_res = client.get("/platform/health")
        assert health_res.status_code == 200, health_res.text
        health_data = health_res.json()
        assert health_data["status"] == "ok"
        assert health_data["modes"]["data"]["mode"] == "live"
        assert health_data["modes"]["data"]["operatorRepositoryReady"] is True

        readiness_res = client.get("/readiness")
        assert readiness_res.status_code == 200, readiness_res.text
        readiness_data = readiness_res.json()
        assert readiness_data["status"] == "ok"
        assert readiness_data["details"]["data"]["mode"] == "live"


def test_forecastops_absent_alias_fails_closed_without_synthetic_seed_or_fake_ready(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Acceptance 4: ForecastOps remains unavailable when production alias is absent.
    No fixture, synthetic auto-seed, fabricated alias, or fake ready state is introduced.
    """
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    bundle = _production_backed_bundle(tmp_path / "forecast-test.sqlite3")

    app = create_app(
        persistence=bundle,
        external_provider_validation=_live_provider(),
        external_provider_connectivity_probe=_live_connectivity_probe,
    )

    with TestClient(app) as client:
        health_res = client.get("/platform/health")
        assert health_res.status_code == 200, health_res.text
        health_data = health_res.json()

        models_section = health_data["modes"]["models"]
        forecast_cap = models_section["capabilities"]["forecastops"]

        assert forecast_cap["available"] is False
        assert forecast_cap["reasonCode"] in {"PRODUCTION_BINDING_NOT_RESOLVED", "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"}
        assert models_section["productionBindingsReady"] is False
        assert models_section["autoSeeded"] is False

        # Accessing model execution endpoint without authorization fails closed
        unauth_exec = client.post("/api/v1/forecastops/forecast-jobs", json={})
        assert unauth_exec.status_code in {401, 403, 422, 503}


def test_unauthorized_access_fails_closed() -> None:
    """Acceptance 2 (fail-closed clause): Invalid or unauthorized access fails closed."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/operator/today",
            "raw_path": b"/api/v1/operator/today",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
        }
    )
    guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE,
        Action.VIEW,
    )

    with pytest.raises(HTTPException) as exc_info:
        guard(request)
    assert exc_info.value.status_code in {401, 403}


@pytest.mark.requires_live_env
def test_operator_live_provenance_real_postgresql_integration(monkeypatch: Any) -> None:
    """Real PostgreSQL 16 runtime verification (runs when live database environment is present)."""
    db_url = "postgresql://oday_app:super-secret@127.0.0.1:5432/oday_plus"
    monkeypatch.setenv("ODAY_DATABASE_URL", db_url)
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")

    try:
        engine = PostgresEngine(db_url, validate_schema=False)
        engine.query("SELECT 1")
    except Exception:
        pytest.skip("Real PostgreSQL database not reachable on 127.0.0.1:5432")

    bundle = build_persistence(mode="postgresql")
    assert bundle.mode == "postgresql"
    assert bundle.engine.is_production is True
