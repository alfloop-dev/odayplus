from __future__ import annotations

import pytest

from apps.data_platform.config import DataPlaneConfig, DataPlaneConfigurationError


def _environment(postgres_dsn: str) -> dict[str, str]:
    return {
        "ODP_DATA_ENV": "production",
        "ODP_DATA_MONGO_URI": "mongodb+srv://service:secret@approved.example/data",
        "ODP_DATA_MONGO_DATABASE": "fongniao_prod",
        "ODP_DATA_POSTGRES_DSN": postgres_dsn,
    }


def test_remote_production_dependencies_are_accepted() -> None:
    config = DataPlaneConfig.from_env(
        _environment("postgresql://service:secret@sql.example/oday")
    )
    assert config.environment == "production"
    assert config.mongo_database == "fongniao_prod"


@pytest.mark.parametrize(
    "postgres_dsn",
    [
        "sqlite:///tmp/oday.db",
        "postgresql://service:secret@localhost/oday",
        "postgresql://service:secret@127.0.0.1/oday",
    ],
)
def test_unapproved_local_sink_is_rejected(postgres_dsn: str) -> None:
    with pytest.raises(DataPlaneConfigurationError):
        DataPlaneConfig.from_env(_environment(postgres_dsn))


def test_cloud_sql_proxy_localhost_is_accepted_with_complete_evidence() -> None:
    environment = _environment(
        "postgresql://service:secret@127.0.0.1:5432/oday"
    )
    environment.update(
        {
            "ODP_DATA_CLOUD_SQL_PROXY": "true",
            "ODP_DATA_CLOUD_SQL_INSTANCE": "alfaloop-data-project:asia-east1:oday-prod",
            "ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE": "cloud-sql-auth-proxy-sidecar",
        }
    )
    config = DataPlaneConfig.from_env(environment)
    assert config.cloud_sql_proxy is True


def test_cloud_sql_proxy_fails_closed_without_instance_or_connector_evidence() -> None:
    environment = _environment("postgresql://service:secret@localhost/oday")
    environment["ODP_DATA_CLOUD_SQL_PROXY"] = "true"
    with pytest.raises(DataPlaneConfigurationError, match="instance connection name"):
        DataPlaneConfig.from_env(environment)


def test_cloud_sql_unix_socket_must_match_expected_instance() -> None:
    environment = _environment(
        "postgresql:///oday?host=/cloudsql/wrong:asia-east1:instance"
    )
    environment.update(
        {
            "ODP_DATA_CLOUD_SQL_PROXY": "true",
            "ODP_DATA_CLOUD_SQL_INSTANCE": "right:asia-east1:instance",
            "ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE": "cloud-sql-python-connector",
        }
    )
    with pytest.raises(DataPlaneConfigurationError, match="does not match"):
        DataPlaneConfig.from_env(environment)


def test_non_production_has_no_fallback() -> None:
    environment = _environment("postgresql://service:secret@sql.example/oday")
    environment["ODP_DATA_ENV"] = "development"
    with pytest.raises(DataPlaneConfigurationError, match="production-only"):
        DataPlaneConfig.from_env(environment)
