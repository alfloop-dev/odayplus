from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_PLACEHOLDER_TOKENS = (
    "<",
    ">",
    "change-me",
    "changeme",
    "example",
    "placeholder",
    "your-",
)
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
_CLOUD_SQL_INSTANCE_RE = re.compile(
    r"^[a-z][a-z0-9-]{4,29}:[a-z0-9-]+:[a-z][a-z0-9-]+$"
)


class MlflowServerSettingsError(RuntimeError):
    """Raised when MLflow is not bound to production-grade remote stores."""


@dataclass(frozen=True)
class MlflowServerSettings:
    backend_store_uri: str
    default_artifact_root: str
    allowed_hosts: str
    cloud_sql_instance: str = ""
    host: str = "0.0.0.0"
    port: int = 5000
    workers: int = 2

    @classmethod
    def from_environment(cls) -> MlflowServerSettings:
        backend = os.getenv("MLFLOW_BACKEND_STORE_URI", "").strip()
        artifact_root = os.getenv("MLFLOW_DEFAULT_ARTIFACT_ROOT", "").strip()
        allowed_hosts = os.getenv(
            "MLFLOW_ALLOWED_HOSTS",
            os.getenv("MLFLOW_SERVER_ALLOWED_HOSTS", ""),
        ).strip()
        cloud_sql_instance = os.getenv("ODP_MLFLOW_CLOUD_SQL_INSTANCE", "").strip()
        host = os.getenv("MLFLOW_HOST", "0.0.0.0").strip()
        try:
            port = int(os.getenv("PORT", os.getenv("MLFLOW_PORT", "5000")))
            workers = int(os.getenv("MLFLOW_WORKERS", "2"))
        except ValueError as exc:
            raise MlflowServerSettingsError(
                "MLFLOW_PORT/PORT and MLFLOW_WORKERS must be integers"
            ) from exc
        settings = cls(
            backend_store_uri=backend,
            default_artifact_root=artifact_root,
            allowed_hosts=allowed_hosts,
            cloud_sql_instance=cloud_sql_instance,
            host=host,
            port=port,
            workers=workers,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        _require_remote_postgresql(
            self.backend_store_uri,
            cloud_sql_instance=self.cloud_sql_instance,
        )
        _require_gcs_root(self.default_artifact_root)
        if not self.allowed_hosts or self.allowed_hosts == "*":
            raise MlflowServerSettingsError(
                "MLFLOW_ALLOWED_HOSTS must explicitly allow trusted service hosts"
            )
        _reject_placeholder(self.allowed_hosts, "MLFLOW_ALLOWED_HOSTS")
        if self.host not in {"0.0.0.0", "::"}:
            raise MlflowServerSettingsError(
                "production MLflow must listen on all container interfaces"
            )
        if not 1 <= self.port <= 65535:
            raise MlflowServerSettingsError("MLflow port must be between 1 and 65535")
        if not 1 <= self.workers <= 32:
            raise MlflowServerSettingsError("MLFLOW_WORKERS must be between 1 and 32")

    def server_command(self) -> tuple[str, ...]:
        return (
            "mlflow",
            "server",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--workers",
            str(self.workers),
            "--allowed-hosts",
            self.allowed_hosts,
            "--no-serve-artifacts",
        )

    def redacted_summary(self) -> dict[str, str | int]:
        backend = urlparse(self.backend_store_uri)
        artifacts = urlparse(self.default_artifact_root)
        return {
            "backend_scheme": backend.scheme,
            "backend_host": backend.hostname or "",
            "backend_database": backend.path.lstrip("/"),
            "artifact_scheme": artifacts.scheme,
            "artifact_bucket": artifacts.netloc,
            "artifact_prefix": artifacts.path.lstrip("/"),
            "allowed_hosts": self.allowed_hosts,
            "host": self.host,
            "port": self.port,
            "workers": self.workers,
        }


def _require_remote_postgresql(value: str, *, cloud_sql_instance: str = "") -> None:
    if not value:
        raise MlflowServerSettingsError("MLFLOW_BACKEND_STORE_URI is required")
    _reject_placeholder(value, "MLFLOW_BACKEND_STORE_URI")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"postgres", "postgresql"}:
        raise MlflowServerSettingsError(
            "MLFLOW_BACKEND_STORE_URI must use remote PostgreSQL"
        )
    hostname = (parsed.hostname or "").lower()
    if hostname == "":
        expected_socket = (
            f"/cloudsql/{cloud_sql_instance}"
            if _CLOUD_SQL_INSTANCE_RE.fullmatch(cloud_sql_instance)
            else ""
        )
        socket_hosts = parse_qs(parsed.query).get("host", [])
        if socket_hosts != [expected_socket] or not expected_socket:
            raise MlflowServerSettingsError(
                "MLFLOW_BACKEND_STORE_URI local socket requires an exact "
                "ODP_MLFLOW_CLOUD_SQL_INSTANCE binding"
            )
    elif hostname in _LOCAL_HOSTS:
        raise MlflowServerSettingsError(
            "MLFLOW_BACKEND_STORE_URI rejects localhost"
        )
    if not parsed.path or parsed.path == "/":
        raise MlflowServerSettingsError(
            "MLFLOW_BACKEND_STORE_URI must name a PostgreSQL database"
        )


def _require_gcs_root(value: str) -> None:
    if not value:
        raise MlflowServerSettingsError("MLFLOW_DEFAULT_ARTIFACT_ROOT is required")
    _reject_placeholder(value, "MLFLOW_DEFAULT_ARTIFACT_ROOT")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "gs" or not parsed.netloc:
        raise MlflowServerSettingsError(
            "MLFLOW_DEFAULT_ARTIFACT_ROOT must be a gs:// bucket prefix"
        )
    if not parsed.path.strip("/"):
        raise MlflowServerSettingsError(
            "MLFLOW_DEFAULT_ARTIFACT_ROOT must include a dedicated prefix"
        )


def _reject_placeholder(value: str, field_name: str) -> None:
    lowered = value.lower()
    if any(token in lowered for token in _PLACEHOLDER_TOKENS):
        raise MlflowServerSettingsError(f"{field_name} contains placeholder material")


__all__ = ["MlflowServerSettings", "MlflowServerSettingsError"]
