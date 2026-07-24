from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qs
from urllib.parse import urlparse


class DataPlaneConfigurationError(RuntimeError):
    """Raised when a production data-plane dependency is missing or unsafe."""


def _required(environment: dict[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise DataPlaneConfigurationError(f"{name} is required")
    return value


@dataclass(frozen=True)
class DataPlaneConfig:
    mongo_uri: str
    postgres_dsn: str
    mongo_database: str = "fongniao_prod"
    raw_schema: str = "fongniao_raw"
    control_schema: str = "data_plane"
    batch_size: int = 5_000
    max_records_per_run: int = 250_000
    mongo_socket_timeout_ms: int = 120_000
    mongo_connect_timeout_ms: int = 15_000
    status_mapping_path: str | None = None
    cloud_sql_proxy: bool = False
    cloud_sql_instance: str | None = None
    cloud_sql_connector_evidence: str | None = None
    environment: str = "production"

    @classmethod
    def from_env(cls, environment: dict[str, str] | None = None) -> DataPlaneConfig:
        values = dict(os.environ if environment is None else environment)
        config = cls(
            environment=_required(values, "ODP_DATA_ENV").lower(),
            mongo_uri=_required(values, "ODP_DATA_MONGO_URI"),
            mongo_database=values.get("ODP_DATA_MONGO_DATABASE", "fongniao_prod").strip(),
            postgres_dsn=(
                values.get("ODP_DATA_POSTGRES_DSN")
                or _required(values, "ODAY_POSTGRES_DSN")
            ),
            raw_schema=values.get("ODP_DATA_RAW_SCHEMA", "fongniao_raw").strip(),
            control_schema=values.get("ODP_DATA_CONTROL_SCHEMA", "data_plane").strip(),
            batch_size=int(values.get("ODP_DATA_BATCH_SIZE", "5000")),
            max_records_per_run=int(values.get("ODP_DATA_MAX_RECORDS_PER_RUN", "250000")),
            mongo_socket_timeout_ms=int(
                values.get("ODP_DATA_MONGO_SOCKET_TIMEOUT_MS", "120000")
            ),
            mongo_connect_timeout_ms=int(
                values.get("ODP_DATA_MONGO_CONNECT_TIMEOUT_MS", "15000")
            ),
            status_mapping_path=values.get("ODP_DATA_STATUS_MAPPING_PATH") or None,
            cloud_sql_proxy=values.get("ODP_DATA_CLOUD_SQL_PROXY", "").lower()
            in {"1", "true", "yes"},
            cloud_sql_instance=values.get("ODP_DATA_CLOUD_SQL_INSTANCE") or None,
            cloud_sql_connector_evidence=values.get(
                "ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE"
            )
            or None,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.environment != "production":
            raise DataPlaneConfigurationError(
                "The Mongo to PostgreSQL data plane is production-only"
            )
        if self.mongo_database != "fongniao_prod":
            raise DataPlaneConfigurationError(
                "ODP_DATA_MONGO_DATABASE must be the approved fongniao_prod database"
            )
        mongo = urlparse(self.mongo_uri)
        if mongo.scheme not in {"mongodb", "mongodb+srv"}:
            raise DataPlaneConfigurationError("ODP_DATA_MONGO_URI must be a MongoDB URI")
        mongo_host = (mongo.hostname or "").lower()
        if mongo_host in {"", "localhost", "127.0.0.1", "::1"}:
            raise DataPlaneConfigurationError("Local MongoDB is not a production source")
        postgres = urlparse(self.postgres_dsn)
        if postgres.scheme not in {"postgres", "postgresql"}:
            raise DataPlaneConfigurationError(
                "ODP_DATA_POSTGRES_DSN must target PostgreSQL"
            )
        postgres_host = (postgres.hostname or "").lower()
        query_host = parse_qs(postgres.query).get("host", [""])[0]
        local_transport = postgres_host in {
            "",
            "localhost",
            "127.0.0.1",
            "::1",
        } or query_host.startswith("/cloudsql/")
        if local_transport:
            allowed_connectors = {
                "cloud-sql-auth-proxy-sidecar",
                "cloud-sql-python-connector",
            }
            instance = str(self.cloud_sql_instance or "")
            if (
                not self.cloud_sql_proxy
                or instance.count(":") != 2
                or self.cloud_sql_connector_evidence not in allowed_connectors
            ):
                raise DataPlaneConfigurationError(
                    "Local PostgreSQL transport requires an approved Cloud SQL "
                    "proxy/connector, instance connection name, and connector evidence"
                )
            if query_host.startswith("/cloudsql/") and instance not in query_host:
                raise DataPlaneConfigurationError(
                    "Cloud SQL unix socket does not match ODP_DATA_CLOUD_SQL_INSTANCE"
                )
        for name, value in (
            ("raw_schema", self.raw_schema),
            ("control_schema", self.control_schema),
        ):
            if not value.replace("_", "").isalnum() or not value[0].isalpha():
                raise DataPlaneConfigurationError(f"Invalid {name}: {value!r}")
        if not 100 <= self.batch_size <= 20_000:
            raise DataPlaneConfigurationError(
                "ODP_DATA_BATCH_SIZE must be between 100 and 20000"
            )
        if not self.batch_size <= self.max_records_per_run <= 5_000_000:
            raise DataPlaneConfigurationError(
                "ODP_DATA_MAX_RECORDS_PER_RUN must be between batch size and 5000000"
            )
        if self.mongo_socket_timeout_ms < 10_000:
            raise DataPlaneConfigurationError(
                "ODP_DATA_MONGO_SOCKET_TIMEOUT_MS must be at least 10000"
            )
        if self.mongo_connect_timeout_ms < 1_000:
            raise DataPlaneConfigurationError(
                "ODP_DATA_MONGO_CONNECT_TIMEOUT_MS must be at least 1000"
            )
