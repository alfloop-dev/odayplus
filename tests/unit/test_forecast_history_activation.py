"""Contract tests for the ForecastOps authoritative history activation module."""

from __future__ import annotations

import pytest

from scripts.data_plane.forecast_history_activation import (
    ACTIVATION_RELATIONS,
    ActivationConfig,
    ForecastHistoryActivationError,
    parse_horizons,
    require_activation_dsn,
    resolve_copy_columns,
)

REMOTE_SOURCE = "postgresql://oday_app:secret@10.20.30.40:5432/oday_app"
REMOTE_TARGET = "postgresql://oday_app:secret@10.20.30.41:5432/oday_app"
PROXY_SOURCE = "postgresql://oday_app:secret@127.0.0.1:6432/oday_app"
SOURCE_INSTANCE = "alfaloop-data-project:asia-east1:oday-plus-dev-postgres"


def _proxy_env(**overrides: str) -> dict[str, str]:
    env = {
        "ODP_LEGACY_DATABASE_URL": PROXY_SOURCE,
        "ODAY_DATABASE_URL": REMOTE_TARGET,
        "ODP_ACTIVATION_CLOUD_SQL_PROXY": "true",
        "ODP_ACTIVATION_CONNECTOR_EVIDENCE": "cloud-sql-auth-proxy-sidecar",
        "ODP_ACTIVATION_SOURCE_INSTANCE": SOURCE_INSTANCE,
    }
    env.update(overrides)
    return env


def test_remote_dsn_needs_no_proxy_attestation() -> None:
    assert (
        require_activation_dsn(
            REMOTE_SOURCE,
            field="ODP_LEGACY_DATABASE_URL",
            instance_env="ODP_ACTIVATION_SOURCE_INSTANCE",
            env={},
        )
        == REMOTE_SOURCE
    )


def test_local_dsn_without_proxy_attestation_fails_closed() -> None:
    with pytest.raises(ForecastHistoryActivationError, match="local transport"):
        require_activation_dsn(
            PROXY_SOURCE,
            field="ODP_LEGACY_DATABASE_URL",
            instance_env="ODP_ACTIVATION_SOURCE_INSTANCE",
            env={},
        )


def test_local_dsn_rejects_unapproved_connector_evidence() -> None:
    env = _proxy_env(ODP_ACTIVATION_CONNECTOR_EVIDENCE="ssh-tunnel")
    with pytest.raises(ForecastHistoryActivationError, match="CONNECTOR_EVIDENCE"):
        require_activation_dsn(
            PROXY_SOURCE,
            field="ODP_LEGACY_DATABASE_URL",
            instance_env="ODP_ACTIVATION_SOURCE_INSTANCE",
            env=env,
        )


def test_cloud_sql_socket_must_match_declared_instance() -> None:
    socket_dsn = (
        "postgresql://oday_app:secret@/oday_app"
        "?host=/cloudsql/alfaloop-data-project:asia-east1:oday-plus-dev-postgres"
    )
    env = _proxy_env(ODP_ACTIVATION_SOURCE_INSTANCE="alfaloop-data-project:asia-east1:other")
    with pytest.raises(ForecastHistoryActivationError, match="does not match"):
        require_activation_dsn(
            socket_dsn,
            field="ODP_LEGACY_DATABASE_URL",
            instance_env="ODP_ACTIVATION_SOURCE_INSTANCE",
            env=env,
        )


def test_non_postgres_and_unnamed_database_fail_closed() -> None:
    with pytest.raises(ForecastHistoryActivationError, match="PostgreSQL URL"):
        require_activation_dsn(
            "mysql://oday_app:secret@10.0.0.1:3306/oday_app",
            field="ODAY_DATABASE_URL",
            instance_env="ODP_ACTIVATION_TARGET_INSTANCE",
            env={},
        )
    with pytest.raises(ForecastHistoryActivationError, match="must name a database"):
        require_activation_dsn(
            "postgresql://oday_app:secret@10.0.0.1:5432/",
            field="ODAY_DATABASE_URL",
            instance_env="ODP_ACTIVATION_TARGET_INSTANCE",
            env={},
        )


def test_config_from_env_accepts_approved_proxy_transport() -> None:
    config = ActivationConfig.from_env(_proxy_env())
    assert config.source_dsn == PROXY_SOURCE
    assert config.target_dsn == REMOTE_TARGET


def test_config_requires_both_endpoints() -> None:
    env = _proxy_env()
    env.pop("ODAY_DATABASE_URL")
    with pytest.raises(ForecastHistoryActivationError, match="ODAY_DATABASE_URL is required"):
        ActivationConfig.from_env(env)


def test_resolve_copy_columns_uses_target_order_and_shared_columns() -> None:
    target = {
        "transaction_id": (False, True),
        "store_id": (False, False),
        "net_amount": (False, True),
        "created_at": (False, True),
    }
    source = {
        "net_amount": (False, True),
        "store_id": (False, False),
        "transaction_id": (False, True),
        "legacy_only": (True, False),
    }
    assert resolve_copy_columns(target, source, relation="core.transactions") == (
        "transaction_id",
        "store_id",
        "net_amount",
    )


def test_resolve_copy_columns_fails_closed_on_unsatisfiable_target_column() -> None:
    target = {"store_id": (False, False), "tenant_id": (False, False)}
    source = {"store_id": (False, False)}
    with pytest.raises(ForecastHistoryActivationError, match="tenant_id"):
        resolve_copy_columns(target, source, relation="core.stores")


def test_resolve_copy_columns_requires_both_relations() -> None:
    with pytest.raises(ForecastHistoryActivationError, match="target relation"):
        resolve_copy_columns({}, {"a": (True, False)}, relation="core.stores")
    with pytest.raises(ForecastHistoryActivationError, match="source relation"):
        resolve_copy_columns({"a": (True, False)}, {}, relation="core.stores")


def test_parse_horizons_dedupes_and_preserves_order() -> None:
    assert parse_horizons(" 28, 7 ,14, 28 ") == (28, 7, 14)


def test_parse_horizons_fails_closed_on_unusable_input() -> None:
    with pytest.raises(ForecastHistoryActivationError, match="not an integer"):
        parse_horizons("28,two-weeks")
    with pytest.raises(ForecastHistoryActivationError, match="positive day count"):
        parse_horizons("28,0")
    with pytest.raises(ForecastHistoryActivationError, match="at least one horizon"):
        parse_horizons(" , ")


def test_activation_relations_are_dependency_ordered_and_transaction_scoped() -> None:
    order = [relation.qualified for relation in ACTIVATION_RELATIONS]
    assert order.index("core.tenants") < order.index("core.brands")
    assert order.index("core.brands") < order.index("core.stores")
    assert order.index("core.address_locations") < order.index("core.stores")
    assert order.index("core.stores") < order.index("core.machines")
    assert order.index("core.machines") < order.index("core.transactions")
    assert order.index("core.transactions") < order.index("data_plane.ingestion_runs")
    assert order.index("data_plane.ingestion_runs") < order.index("data_plane.canonical_lineage")

    lineage = next(r for r in ACTIVATION_RELATIONS if r.table == "canonical_lineage")
    runs = next(r for r in ACTIVATION_RELATIONS if r.table == "ingestion_runs")
    assert lineage.source_predicate == "canonical_table = 'core.transactions'"
    assert runs.source_predicate is not None
    assert "canonical_lineage" in runs.source_predicate
