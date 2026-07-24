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
from modules.avm.application import AVMProductionExecutor
from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle


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


def _live_provider() -> Any:
    return SimpleNamespace(
        mode=SimpleNamespace(value="live"),
        ok=True,
        errors=(),
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
        learninghub,
        netplan,
        operator,
        priceops,
        sitescore,
    )
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

    bundle = _production_backed_bundle(tmp_path / "production-composition.sqlite3")
    try:
        app = create_app(
            persistence=bundle,
            external_provider_validation=_live_provider(),
        )
    finally:
        bundle.engine.close()

    assert {service for service, _version in runtime.resolutions} == {
        "avm",
        "forecastops",
        "heatzone",
        "sitescore",
    }
    assert captured["model_runtime_factory"]["model_names"] == {
        "avm": "oday-avm",
        "forecastops": "oday-forecastops",
        "heatzone": "oday-heatzone",
        "sitescore": "oday-sitescore",
    }
    assert captured["avm_executor_factory"]["model_runtime"] is runtime

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
    assert captured["avm"]["production_executor"] is avm_executor
    assert captured["netplan"]["repository"] is bundle.netplan_repository
    assert captured["netplan"]["audit_log"] is bundle.audit_log
    assert captured["netplan"]["production_executor"] is not None
    assert captured["priceops"]["repository"] is bundle.priceops_repository
    assert captured["priceops"]["audit_log"] is bundle.audit_log
    assert captured["priceops"]["production_optimizer"] is not None
    assert captured["adlift"]["repository"] is bundle.adlift_repository
    assert captured["adlift"]["audit_log"] is bundle.audit_log
    assert captured["operator"]["model_runtime"] is runtime
    assert captured["operator"]["avm_production_executor"] is avm_executor
    assert captured["operator"]["netplan_production_executor"] is (
        captured["netplan"]["production_executor"]
    )
    assert app.state.domain_runtime_mode == "production"
    assert app.state.production_model_error is None


def test_live_routes_fail_closed_when_production_dependencies_are_missing(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    app = create_app(
        persistence=_memory_bundle(),
        external_provider_validation=_live_provider(),
    )

    with TestClient(app) as client:
        for path in (
            "/api/v1/forecastops/timeseries",
            "/api/v1/learninghub/models",
            "/api/v1/sitescore/reports",
            "/api/v1/avm/cases",
            "/api/v1/netplan/scenarios",
            "/api/v1/priceops/plans",
            "/api/v1/adlift/reports",
        ):
            response = client.get(path)
            assert response.status_code == 503, (path, response.text)
            assert "BINDING_REQUIRED" in response.text
