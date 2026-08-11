from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvloop
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from models.shared_ml.production_contracts import (
    PRODUCTION_MODEL_CONTRACTS,
    production_model_names,
)
from models.shared_ml.production_runtime import ProductionModelRegistryError
from modules.avm.application import AVMProductionExecutor
from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle
from tests.integration._authz import EXTERNAL_DATA_HEADERS


@pytest.fixture(autouse=True)
def _use_production_event_loop_policy() -> Any:
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


class RecordingProductionRuntime:
    tracking_uri = "https://mlflow.internal.example"

    def __init__(self) -> None:
        self.client = object()
        self.resolutions: list[tuple[str, str]] = []
        self.inferences: list[dict[str, Any]] = []

    def resolve(
        self,
        *,
        service: str,
        expected_feature_schema_version: str,
    ) -> Any:
        self.resolutions.append((service, expected_feature_schema_version))
        return SimpleNamespace(
            binding=SimpleNamespace(model_id=f"{service}:production")
        )

    def infer(self, **kwargs: Any) -> Any:
        self.inferences.append(kwargs)
        raise AssertionError("composition test does not execute an HTTP scoring job")


class SelectiveProductionRuntime(RecordingProductionRuntime):
    def __init__(self, *unavailable_services: str) -> None:
        super().__init__()
        self.unavailable_services = frozenset(unavailable_services)

    def resolve(
        self,
        *,
        service: str,
        expected_feature_schema_version: str,
    ) -> Any:
        if service in self.unavailable_services:
            raise ProductionModelRegistryError(
                f"{service}: mature production alias is unavailable"
            )
        return super().resolve(
            service=service,
            expected_feature_schema_version=expected_feature_schema_version,
        )


def _live_provider() -> Any:
    return SimpleNamespace(
        mode=SimpleNamespace(value="live"),
        ok=True,
        errors=(),
    )


def _unavailable_provider() -> Any:
    return SimpleNamespace(
        mode=SimpleNamespace(value="fixture"),
        ok=False,
        errors=("production provider credentials are unavailable",),
    )


def _production_backed_bundle(path: Path) -> Any:
    bundle = _durable_bundle(path)
    bundle.engine.is_production = True
    return replace(
        bundle,
        mode="postgresql",
        assisted_intake_store=SimpleNamespace(),
    )


def test_require_live_data_composes_remote_runtime_and_oss_dependencies(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    runtime = RecordingProductionRuntime()
    avm_executor = SimpleNamespace(execute=lambda *_args, **_kwargs: None)
    captured: dict[str, dict[str, Any]] = {}

    from apps.api.app.routes import (
        adlift,
        avm,
        forecastops,
        interventions,
        learninghub,
        netplan,
        operator,
        priceops,
        sitescore,
    )
    from apps.api.oday_api import main as api_main
    from models.shared_ml import MlflowProductionModelRuntime

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_AVM_MODEL_NAME", "oday-avm")
    monkeypatch.setenv("ODP_FORECASTOPS_MODEL_NAME", "oday-forecastops")
    monkeypatch.setenv("ODP_HEATZONE_MODEL_NAME", "oday-heatzone")
    monkeypatch.setenv("ODP_SITESCORE_MODEL_NAME", "oday-sitescore")

    def build_runtime(
        _cls: type[MlflowProductionModelRuntime],
        **kwargs: Any,
    ) -> RecordingProductionRuntime:
        captured["model_runtime_factory"] = kwargs
        return runtime

    monkeypatch.setattr(
        MlflowProductionModelRuntime,
        "from_environment",
        classmethod(build_runtime),
    )
    monkeypatch.setattr(
        AVMProductionExecutor,
        "from_environment",
        classmethod(
            lambda cls, *, model_runtime=None: (
                captured.setdefault(
                    "avm_executor_factory",
                    {"model_runtime": model_runtime},
                )
                and avm_executor
            )
        ),
    )

    for name, module, attribute in (
        ("forecastops", forecastops, "create_forecastops_router"),
        ("learninghub", learninghub, "create_learninghub_router"),
        ("sitescore", sitescore, "create_sitescore_router"),
        ("avm", avm, "create_avm_router"),
        ("netplan", netplan, "create_netplan_router"),
        ("priceops", priceops, "create_priceops_router"),
        ("adlift", adlift, "create_adlift_router"),
        ("interventions", interventions, "create_interventions_router"),
        ("operator", operator, "create_operator_router"),
    ):
        original = getattr(module, attribute)

        def wrapper(
            _original: Any = original,
            _name: str = name,
            **kwargs: Any,
        ) -> Any:
            captured[_name] = kwargs
            return _original(**kwargs)

        monkeypatch.setattr(module, attribute, wrapper)

    original_heatzone_router = api_main.create_heatzone_router

    def heatzone_wrapper(**kwargs: Any) -> Any:
        captured["heatzone"] = kwargs
        return original_heatzone_router(**kwargs)

    monkeypatch.setattr(api_main, "create_heatzone_router", heatzone_wrapper)

    bundle = _production_backed_bundle(tmp_path / "production-composition.sqlite3")
    try:
        app = create_app(
            persistence=bundle,
            external_provider_validation=_live_provider(),
        )
    finally:
        bundle.engine.close()

    # Only ForecastOps has a resolvable production alias; AVM, HeatZone and
    # SiteScore are governed-disabled and must never reach MLflow resolution.
    assert {service for service, _version in runtime.resolutions} == {"forecastops"}
    assert captured["model_runtime_factory"]["model_names"] == production_model_names()
    assert production_model_names() == {"forecastops": "forecast_revenue_interval"}
    # The AVM production executor is only built for an available AVM binding;
    # governed-disabled AVM composes with no executor and no fabricated alias.
    assert "avm_executor_factory" not in captured

    for name in (
        "forecastops",
        "learninghub",
        "sitescore",
        "avm",
        "netplan",
        "priceops",
        "adlift",
    ):
        assert captured[name]["runtime_mode"] == "production"

    assert captured["forecastops"]["model_runtime"] is runtime
    assert captured["heatzone"]["model_runtime"] is runtime
    assert captured["forecastops"]["repository"] is bundle.forecastops_repository
    assert captured["forecastops"]["audit_log"] is bundle.audit_log
    assert captured["forecastops"]["job_queue"] is bundle.job_queue
    assert captured["sitescore"]["model_runtime"] is runtime
    assert captured["sitescore"]["repository"] is bundle.sitescore_repository
    assert captured["sitescore"]["audit_log"] is bundle.audit_log
    assert captured["sitescore"]["job_queue"] is bundle.job_queue
    assert captured["learninghub"]["repository"] is bundle.learninghub_repository
    assert captured["learninghub"]["registry"].tracking_uri == runtime.tracking_uri
    assert captured["learninghub"]["registry"].client is runtime.client
    assert captured["learninghub"]["artifact_store"] is bundle.artifact_store
    assert captured["learninghub"]["audit_log"] is bundle.audit_log
    assert captured["avm"]["repository"] is bundle.avm_repository
    assert captured["avm"]["audit_log"] is bundle.audit_log
    assert captured["avm"]["job_queue"] is bundle.job_queue
    assert captured["avm"]["require_durable_commands"] is True
    assert captured["avm"]["production_executor"] is None
    assert captured["netplan"]["repository"] is bundle.netplan_repository
    assert captured["netplan"]["audit_log"] is bundle.audit_log
    assert captured["netplan"]["production_executor"] is not None
    assert captured["priceops"]["repository"] is bundle.priceops_repository
    assert captured["priceops"]["audit_log"] is bundle.audit_log
    assert captured["priceops"]["job_queue"] is bundle.job_queue
    assert captured["priceops"]["require_durable_commands"] is True
    assert captured["priceops"]["production_optimizer"] is not None
    assert captured["adlift"]["repository"] is bundle.adlift_repository
    assert captured["adlift"]["audit_log"] is bundle.audit_log
    assert captured["adlift"]["job_queue"] is bundle.job_queue
    assert captured["adlift"]["require_durable_jobs"] is True
    assert captured["interventions"]["job_queue"] is bundle.job_queue
    assert captured["interventions"]["require_durable_commands"] is True
    assert captured["operator"]["model_runtime"] is runtime
    assert captured["operator"]["avm_production_executor"] is None
    assert captured["operator"]["netplan_production_executor"] is (
        captured["netplan"]["production_executor"]
    )
    assert app.state.domain_runtime_mode == "production"
    # Governed-disabled services contribute no composition errors and no
    # scoring bindings; the platform is ready with ForecastOps alone active.
    assert app.state.production_model_error is None
    assert app.state.scoring_bindings.keys() == {"forecastops"}
    heatzone_capability = app.state.production_model_capabilities["heatzone"]
    assert heatzone_capability["modelName"] == "heatzone_priority"
    assert heatzone_capability["trainable"] is True
    assert heatzone_capability["requiredForPlatformReadiness"] is True
    assert heatzone_capability["available"] is False
    assert heatzone_capability["governedDisabled"] is True
    assert heatzone_capability["reasonCode"] == "DATA_CONTRACT_NOT_MATURE"
    evidence = heatzone_capability["governedDisabledEvidence"]
    assert evidence["reasonCode"] == heatzone_capability["reasonCode"]
    assert evidence["autoSeeded"] is False
    assert evidence["observedCount"] < evidence["activationThreshold"]
    assert evidence["observedAt"] and evidence["inventoryVersion"]


def test_governed_disabled_services_stay_fail_closed_while_platform_is_ready(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Governed-disabled services are never resolved and never fabricate aliases.

    Even against a registry that *would* resolve them (the runtime double here
    resolves anything it is asked for), composition must not ask: the
    governed-disabled evidence is the capability's terminal state this cycle,
    and readiness comes from that evidence, not from an alias.
    """
    runtime = RecordingProductionRuntime()
    from models.shared_ml import MlflowProductionModelRuntime

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        MlflowProductionModelRuntime,
        "from_environment",
        classmethod(lambda _cls, **_kwargs: runtime),
    )

    bundle = _production_backed_bundle(tmp_path / "partial-models.sqlite3")
    try:
        app = create_app(
            persistence=bundle,
            external_provider_validation=_live_provider(),
        )
    finally:
        bundle.engine.close()

    capabilities = app.state.production_model_capabilities
    assert {service for service, _version in runtime.resolutions} == {"forecastops"}
    assert app.state.scoring_bindings.keys() == {"forecastops"}
    assert capabilities["forecastops"]["available"] is True
    for service in ("avm", "heatzone", "sitescore"):
        assert capabilities[service]["available"] is False
        assert capabilities[service]["governedDisabled"] is True
        assert capabilities[service]["reasonCode"] == "DATA_CONTRACT_NOT_MATURE"
        assert capabilities[service]["error"] is None
        evidence = capabilities[service]["governedDisabledEvidence"]
        assert evidence["reasonCode"] == capabilities[service]["reasonCode"]
        assert evidence["autoSeeded"] is False
    assert app.state.production_model_error is None


def test_forecastops_alias_remains_a_required_fail_closed_binding(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    runtime = SelectiveProductionRuntime("forecastops")
    from models.shared_ml import MlflowProductionModelRuntime

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        MlflowProductionModelRuntime,
        "from_environment",
        classmethod(lambda _cls, **_kwargs: runtime),
    )

    bundle = _production_backed_bundle(tmp_path / "forecast-required.sqlite3")
    try:
        app = create_app(
            persistence=bundle,
            external_provider_validation=_live_provider(),
        )
    finally:
        bundle.engine.close()

    # No governed-disabled service may backfill readiness when ForecastOps,
    # the one required-active binding, fails to resolve.
    assert app.state.scoring_bindings.keys() == set()
    assert app.state.production_model_capabilities["forecastops"]["available"] is False
    assert "forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE" in (
        app.state.production_model_error or ""
    )


def test_training_and_runtime_share_canonical_production_model_names() -> None:
    from scripts.models.contracts import MODEL_SPECS

    # Runtime alias resolution only covers non-governed-disabled services...
    assert {
        service: MODEL_SPECS[contract.training_spec_key].model_name
        for service, contract in PRODUCTION_MODEL_CONTRACTS.items()
        if contract.training_spec_key is not None
        and not contract.is_governed_disabled
    } == production_model_names()
    # ...but every governed-disabled contract keeps its trainer binding so
    # activation (once counts pass the threshold) needs no new wiring.
    for contract in PRODUCTION_MODEL_CONTRACTS.values():
        if contract.is_governed_disabled:
            assert contract.trainable is True
            assert MODEL_SPECS[contract.training_spec_key].model_name == (
                contract.model_name
            )
    assert PRODUCTION_MODEL_CONTRACTS["heatzone"].trainable is True


def test_live_routes_fail_closed_when_production_dependencies_are_missing(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    bundle = _production_backed_bundle(tmp_path / "model-gate.sqlite3")
    app = create_app(
        persistence=bundle,
        external_provider_validation=_live_provider(),
    )

    try:
        with TestClient(app) as client:
            for path in (
                "/api/v1/forecastops/timeseries",
                "/api/v1/learninghub/models",
                "/api/v1/sitescore/reports",
                "/api/v1/avm/cases",
            ):
                response = client.get(path)
                assert response.status_code == 503, (path, response.text)
                assert "BINDING_REQUIRED" in response.text
    finally:
        bundle.engine.close()


def test_production_routes_gate_only_the_dependency_they_use(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    bundle = _production_backed_bundle(tmp_path / "scoped-gate.sqlite3")
    app = create_app(
        persistence=bundle,
        external_provider_validation=_unavailable_provider(),
    )
    operator_headers = {
        "x-subject-id": "production-operator",
        "x-roles": "operations_manager",
        "x-tenant-id": "tenant-a",
        "x-operator-role": "ops-lead",
    }

    try:
        with TestClient(app) as client:
            operator = client.get(
                "/api/v1/operator/bootstrap",
                headers=operator_headers,
            )
            assert operator.status_code == 200, operator.text
            assert operator.json()["meta"]["dataMode"] == "live"
            assert "r4-seed" not in operator.text

            history = client.get(
                "/api/v1/external-data/ingestion-runs",
                headers=EXTERNAL_DATA_HEADERS,
            )
            assert history.status_code == 200
            assert history.json() == {"items": [], "count": 0}

            provider_route = client.post(
                "/api/v1/external-data/ingestion-runs",
                headers=EXTERNAL_DATA_HEADERS,
                json={"provider_id": "listing.partner_feed"},
            )
            assert provider_route.status_code == 503
            assert (
                provider_route.json()["error"]["code"]
                == "external_provider_unavailable"
            )

            model_route = client.get("/api/v1/forecastops/timeseries")
            assert model_route.status_code == 503
            assert "BINDING_REQUIRED" in model_route.text
    finally:
        bundle.engine.close()


def test_non_production_fixture_ingestion_is_unchanged(monkeypatch: Any) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    monkeypatch.setenv("ODP_PRODUCT_MODE", "test")
    monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "fixture")
    app = create_app(persistence=_memory_bundle())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/external-data/ingestion-runs",
            headers={
                **EXTERNAL_DATA_HEADERS,
                "Idempotency-Key": "local-fixture-regression",
            },
            json={"provider_id": "listing.partner_feed"},
        )

    assert response.status_code == 202
    assert response.json()["created"] is True
    assert response.json()["accepted_count"] == 2


def test_external_ingestion_uses_bundle_scheduler_state_store() -> None:
    bundle = _memory_bundle()

    app = create_app(persistence=bundle)

    service = app.state.external_ingestion_service
    assert service.store is bundle.ingestion_run_store
    assert service.scheduler.state_store is bundle.external_fetch_state_store
