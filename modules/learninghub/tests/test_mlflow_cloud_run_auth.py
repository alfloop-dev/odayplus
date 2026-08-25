from __future__ import annotations

import os

import pytest
from requests import Request

from modules.learninghub.infrastructure.mlflow_cloud_run_auth import (
    CLOUD_RUN_MLFLOW_AUDIENCE_ENV,
    CLOUD_RUN_MLFLOW_AUTH_NAME,
    CloudRunIdTokenAuth,
    CloudRunMlflowRequestAuthProvider,
    configure_cloud_run_mlflow_auth,
)
from modules.learninghub.runtime import LearningHubRuntimeConfigurationError


def test_cloud_run_auth_injects_and_boundedly_caches_identity_token() -> None:
    now = [100.0]
    calls: list[str] = []

    def fetch(audience: str) -> str:
        calls.append(audience)
        return f"token-{len(calls)}"

    auth = CloudRunIdTokenAuth(
        audience="https://oday-mlflow-123.asia-east1.run.app",
        token_fetcher=fetch,
        clock=lambda: now[0],
        cache_seconds=60,
    )

    first = auth(Request("GET", "https://oday-mlflow-123.asia-east1.run.app/api").prepare())
    now[0] = 159.0
    second = auth(Request("POST", "https://oday-mlflow-123.asia-east1.run.app/api").prepare())
    now[0] = 160.0
    third = auth(Request("GET", "https://oday-mlflow-123.asia-east1.run.app/api").prepare())

    assert first.headers["Authorization"] == "Bearer token-1"
    assert second.headers["Authorization"] == "Bearer token-1"
    assert third.headers["Authorization"] == "Bearer token-2"
    assert calls == [
        "https://oday-mlflow-123.asia-east1.run.app",
        "https://oday-mlflow-123.asia-east1.run.app",
    ]


@pytest.mark.parametrize(
    "audience",
    [
        "",
        "http://oday-mlflow-123.asia-east1.run.app",
        "https://mlflow.example.test",
        "https://oday-mlflow-123.asia-east1.run.app/path",
        "https://oday-mlflow-123.asia-east1.run.app?query=yes",
    ],
)
def test_cloud_run_auth_rejects_non_exact_cloud_run_origins(audience: str) -> None:
    with pytest.raises(
        LearningHubRuntimeConfigurationError,
        match="exact HTTPS Cloud Run service origin",
    ):
        CloudRunIdTokenAuth(audience=audience)


def test_configuration_requires_one_auth_mechanism_and_matching_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = "https://oday-mlflow-123.asia-east1.run.app"
    monkeypatch.delenv("MLFLOW_TRACKING_AUTH", raising=False)

    configure_cloud_run_mlflow_auth(
        tracking_uri=uri,
        environment={"ODP_MLFLOW_CLOUD_RUN_AUDIENCE": uri},
    )
    assert os.environ["MLFLOW_TRACKING_AUTH"] == CLOUD_RUN_MLFLOW_AUTH_NAME
    assert os.environ["ODP_MLFLOW_CLOUD_RUN_AUDIENCE"] == uri

    with pytest.raises(LearningHubRuntimeConfigurationError, match="exactly match"):
        configure_cloud_run_mlflow_auth(
            tracking_uri=uri,
            environment={
                "ODP_MLFLOW_CLOUD_RUN_AUDIENCE": (
                    "https://other-mlflow-123.asia-east1.run.app"
                )
            },
        )

    with pytest.raises(LearningHubRuntimeConfigurationError, match="second authentication"):
        configure_cloud_run_mlflow_auth(
            tracking_uri=uri,
            environment={
                "ODP_MLFLOW_CLOUD_RUN_AUDIENCE": uri,
                "MLFLOW_TRACKING_AUTH": "basic-auth",
            },
        )


def test_mlflow_provider_reuses_auth_object_for_token_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_uri = "https://oday-mlflow-123.asia-east1.run.app"
    second_uri = "https://oday-mlflow-456.asia-east1.run.app"
    provider = CloudRunMlflowRequestAuthProvider()

    monkeypatch.setenv(CLOUD_RUN_MLFLOW_AUDIENCE_ENV, first_uri)
    first = provider.get_auth()
    assert provider.get_auth() is first

    monkeypatch.setenv(CLOUD_RUN_MLFLOW_AUDIENCE_ENV, second_uri)
    second = provider.get_auth()
    assert second is not first
    assert isinstance(second, CloudRunIdTokenAuth)
    assert second.audience == second_uri
