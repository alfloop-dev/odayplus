from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.id_token import fetch_id_token
from mlflow.tracking.request_auth.abstract_request_auth_provider import RequestAuthProvider
from requests import PreparedRequest
from requests.auth import AuthBase

from ..runtime import LearningHubRuntimeConfigurationError

CLOUD_RUN_MLFLOW_AUTH_NAME = "oday-cloud-run-id-token"
CLOUD_RUN_MLFLOW_AUDIENCE_ENV = "ODP_MLFLOW_CLOUD_RUN_AUDIENCE"
MLFLOW_TRACKING_AUTH_ENV = "MLFLOW_TRACKING_AUTH"
_TOKEN_CACHE_SECONDS = 240.0


def _fetch_google_identity_token(audience: str) -> str:
    return fetch_id_token(GoogleAuthRequest(), audience)


def _cloud_run_origin(value: str, *, field_name: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".run.app")
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise LearningHubRuntimeConfigurationError(
            f"{field_name} must be an exact HTTPS Cloud Run service origin"
        )
    return candidate


@dataclass
class CloudRunIdTokenAuth(AuthBase):
    audience: str
    token_fetcher: Callable[[str], str] = field(
        default=_fetch_google_identity_token,
        repr=False,
    )
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    cache_seconds: float = _TOKEN_CACHE_SECONDS
    _token: str = field(default="", init=False, repr=False)
    _refresh_at: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.audience = _cloud_run_origin(
            self.audience,
            field_name=CLOUD_RUN_MLFLOW_AUDIENCE_ENV,
        )
        if self.cache_seconds <= 0:
            raise LearningHubRuntimeConfigurationError(
                "Cloud Run MLflow identity-token cache must be positive"
            )

    def _current_token(self) -> str:
        now = self.clock()
        if self._token and now < self._refresh_at:
            return self._token
        with self._lock:
            now = self.clock()
            if self._token and now < self._refresh_at:
                return self._token
            token = self.token_fetcher(self.audience).strip()
            if not token:
                raise LearningHubRuntimeConfigurationError(
                    "Google workload identity returned an empty MLflow ID token"
                )
            self._token = token
            self._refresh_at = now + self.cache_seconds
            return token

    def __call__(self, request: PreparedRequest) -> PreparedRequest:
        request.headers["Authorization"] = f"Bearer {self._current_token()}"
        return request


class CloudRunMlflowRequestAuthProvider(RequestAuthProvider):
    def __init__(self) -> None:
        self._auth: CloudRunIdTokenAuth | None = None
        self._audience = ""
        self._lock = threading.Lock()

    def get_name(self) -> str:
        return CLOUD_RUN_MLFLOW_AUTH_NAME

    def get_auth(self) -> AuthBase:
        audience = os.environ.get(CLOUD_RUN_MLFLOW_AUDIENCE_ENV, "")
        with self._lock:
            if self._auth is None or audience != self._audience:
                self._auth = CloudRunIdTokenAuth(audience=audience)
                self._audience = self._auth.audience
            return self._auth


def configure_cloud_run_mlflow_auth(
    *,
    tracking_uri: str,
    environment: Mapping[str, str] | None = None,
) -> None:
    env = os.environ if environment is None else environment
    tracking_origin = _cloud_run_origin(tracking_uri, field_name="MLFLOW_TRACKING_URI")
    audience = _cloud_run_origin(
        env.get(CLOUD_RUN_MLFLOW_AUDIENCE_ENV, ""),
        field_name=CLOUD_RUN_MLFLOW_AUDIENCE_ENV,
    )
    if tracking_origin != audience:
        raise LearningHubRuntimeConfigurationError(
            "ODP_MLFLOW_CLOUD_RUN_AUDIENCE must exactly match MLFLOW_TRACKING_URI"
        )

    selected_auth = env.get(MLFLOW_TRACKING_AUTH_ENV, "").strip()
    if selected_auth and selected_auth != CLOUD_RUN_MLFLOW_AUTH_NAME:
        raise LearningHubRuntimeConfigurationError(
            "MLFLOW_TRACKING_AUTH selects a second authentication mechanism"
        )

    # MLflow's RequestAuthProvider API is stable; its process-local registry is
    # intentionally populated once before MlflowClient resolves host creds.
    from mlflow.tracking.request_auth.registry import _request_auth_provider_registry

    if not any(
        provider.get_name() == CLOUD_RUN_MLFLOW_AUTH_NAME
        for provider in _request_auth_provider_registry
    ):
        _request_auth_provider_registry.register(CloudRunMlflowRequestAuthProvider)
    os.environ[CLOUD_RUN_MLFLOW_AUDIENCE_ENV] = audience
    os.environ[MLFLOW_TRACKING_AUTH_ENV] = CLOUD_RUN_MLFLOW_AUTH_NAME


__all__ = [
    "CLOUD_RUN_MLFLOW_AUDIENCE_ENV",
    "CLOUD_RUN_MLFLOW_AUTH_NAME",
    "CloudRunIdTokenAuth",
    "CloudRunMlflowRequestAuthProvider",
    "configure_cloud_run_mlflow_auth",
]
