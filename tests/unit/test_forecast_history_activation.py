"""Contract tests for the ForecastOps authoritative history activation module."""

from __future__ import annotations

import pytest

from scripts.data_plane.forecast_history_activation import (
    ACTIVATION_RELATIONS,
    ActivationConfig,
    ForecastHistoryActivationError,
    Relation,
    parse_horizons,
    prune_sql,
    refresh_sql,
    require_activation_dsn,
    resolve_copy_columns,
    source_catalog,
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


class _FakeCursor:
    """Minimal psycopg cursor stand-in returning one canned aggregate row."""

    def __init__(self, rows: list[tuple], description: tuple[str, ...] | None) -> None:
        self._rows = rows
        self.description = (
            tuple(_FakeColumn(name) for name in description) if description else None
        )

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class _FakeColumn:
    def __init__(self, name: str) -> None:
        self.name = name


class _CatalogConnection:
    """Answers relation-existence probes from ``present`` and nothing else."""

    def __init__(self, present: set[tuple[str, str]]) -> None:
        self.present = present
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple | None = None) -> _FakeCursor:
        self.statements.append(sql)
        if "information_schema.tables" in sql and params is not None:
            schema, table = params[0], params[1]
            return _FakeCursor([((schema, table) in self.present,)], None)
        # Every catalog aggregate is answered with an empty, well-shaped result.
        if "count(*)" in sql and "GROUP BY" not in sql:
            return _FakeCursor([tuple([0] * 8)], None)
        return _FakeCursor([], ("bucket", "value"))


def test_source_catalog_covers_the_whole_authoritative_chain() -> None:
    connection = _CatalogConnection(
        {
            ("fongniao_raw", "raw_orders"),
            ("data_plane", "ingestion_runs"),
            ("data_plane", "quarantined_records"),
            ("data_plane", "transaction_authority"),
            ("data_plane", "canonical_lineage"),
            ("core", "transactions"),
        }
    )
    catalog = source_catalog(connection)
    assert set(catalog) == {
        "raw_landing",
        "ingestion_runs",
        "quarantine",
        "transaction_authority",
        "canonical_lineage",
        "canonical_transactions",
    }
    assert all(section["available"] for section in catalog.values())


def test_source_catalog_degrades_when_a_source_relation_is_absent() -> None:
    # The canonical target has no raw landing schema; the report must still run.
    connection = _CatalogConnection({("core", "transactions")})
    catalog = source_catalog(connection)
    assert catalog["raw_landing"] == {
        "available": False,
        "relation": "fongniao_raw.raw_orders",
    }
    assert catalog["canonical_transactions"]["available"] is True


def test_every_relation_converges_and_only_lineage_prunes() -> None:
    """The target mirrors the source, and only lineage may drop a row.

    This assertion used to read ``refreshing == {ingestion_runs,
    canonical_lineage}`` under the title "immutable records must never be
    rewritten by a replay". The premise was false and the test was holding it in
    place. ``core.transactions`` is not immutable in the source:
    ``apps/data_platform/store.py`` re-projects a changed upstream record with
    ``ON CONFLICT (transaction_id) DO UPDATE`` across every column
    ``forecast_training_view`` reads. Freezing the target against that is not
    conservatism, it is a stale read -- measured in
    ``canonical_row_drift_audit.json`` as 1 847 drifted transaction rows, one of
    them a source ``refunded``/0.00 that the target still reported as
    ``succeeded``/230.00 and the view still counted as revenue.

    What is genuinely unsafe is DELETION, and that is what stays narrow.
    """

    refreshing = {r.qualified for r in ACTIVATION_RELATIONS if r.refresh_key}
    pruning = {r.qualified for r in ACTIVATION_RELATIONS if r.prune_superseded_by}
    assert refreshing == {r.qualified for r in ACTIVATION_RELATIONS}, (
        "every activation relation must be able to converge on the source; a "
        "relation left without a refresh key freezes at whatever the first copy "
        "saw and can never be corrected"
    )
    assert pruning == {"data_plane.canonical_lineage"}

    runs = next(r for r in ACTIVATION_RELATIONS if r.table == "ingestion_runs")
    lineage = next(r for r in ACTIVATION_RELATIONS if r.table == "canonical_lineage")
    assert runs.refresh_key == ("run_id",)
    assert lineage.prune_superseded_by == ("run_id", "canonical_id")
    assert lineage.prune_keep_key == ("canonical_id",)


def test_core_relations_refresh_on_their_primary_key_and_never_prune() -> None:
    """A ``core`` row may be corrected in place and must never be deleted.

    The refresh key has to be the primary key: it is the only column set that
    matches a target row one-for-one, so anything wider would leave drifted rows
    behind and anything narrower would let one staged row rewrite several
    targets. And no ``core`` relation prunes -- the source selection carries no
    predicate there, so a missing staged row would mean a read failure rather
    than a supersession, and deleting on that basis could strip real history.
    """

    expected = {
        "core.tenants": ("tenant_id",),
        "core.brands": ("brand_id",),
        "core.address_locations": ("address_id",),
        "core.stores": ("store_id",),
        "core.machines": ("machine_id",),
        "core.transactions": ("transaction_id",),
    }
    for relation in ACTIVATION_RELATIONS:
        if relation.schema != "core":
            continue
        assert relation.refresh_key == expected[relation.qualified]
        assert relation.prune_superseded_by == ()
        assert relation.source_predicate is None


def test_every_pruning_relation_can_also_refresh_in_place() -> None:
    """A prune without a refresh can delete a row the insert cannot replace.

    ``canonical_lineage``'s primary key is content-derived, so re-projecting an
    unchanged record under the run that superseded an abandoned one produces a
    row with the SAME key and a new ``run_id``. ``ON CONFLICT DO NOTHING``
    discards it, and the prune then drops the target's old row because no staged
    row matches its ``(run_id, canonical_id)`` while a staged keeper exists for
    its ``canonical_id`` -- stripping the record's only lineage. Measured on the
    live target before this rule existed: 1 841 lineage rows lost, 1 693
    transactions left with none (lineage_activation_loss.json).
    """

    for relation in ACTIVATION_RELATIONS:
        if not relation.prune_superseded_by:
            continue
        assert relation.refresh_key, (
            f"{relation.qualified} prunes superseded rows but cannot re-point "
            "one in place; an updated row keeping its target key would be "
            "deleted with nothing to replace it"
        )


def test_prune_configuration_fails_closed_without_a_keep_key() -> None:
    with pytest.raises(ForecastHistoryActivationError, match="prune_keep_key"):
        Relation("data_plane", "canonical_lineage", prune_superseded_by=("run_id",))


def test_refresh_sql_updates_every_shared_column_outside_the_key() -> None:
    relation = Relation("data_plane", "ingestion_runs", refresh_key=("run_id",))
    statement, updatable = refresh_sql(
        relation, ("run_id", "status", "finished_at"), "activation_stage"
    )
    assert updatable == ("status", "finished_at")
    assert 'UPDATE data_plane.ingestion_runs AS tgt SET "status" = staged."status"' in statement
    assert '"finished_at" = staged."finished_at"' in statement
    assert 'WHERE tgt."run_id" = staged."run_id"' in statement
    # A converged row must not be rewritten.
    assert "IS DISTINCT FROM" in statement


def test_lineage_refresh_repoints_the_run_without_rewriting_the_record() -> None:
    """The lineage refresh must move the pointer and nothing else.

    Matching on the whole primary key is what makes this safe: it can only ever
    touch the row the source is re-projecting, and the record it describes
    (``source_id``, ``content_sha256``, ``tenant_id``) is keyed by content, so
    an updated row that changed any of those would carry a DIFFERENT
    ``source_snapshot_id`` and be inserted rather than refreshed.
    """

    lineage = next(r for r in ACTIVATION_RELATIONS if r.table == "canonical_lineage")
    columns = (
        "source_snapshot_id",
        "source_kind",
        "source_id",
        "content_sha256",
        "run_id",
        "tenant_id",
        "canonical_table",
        "canonical_id",
        "projected_at",
    )
    statement, updatable = refresh_sql(lineage, columns, "activation_stage")
    assert "run_id" in updatable
    # The primary key is matched, never assigned.
    for key in ("source_snapshot_id", "canonical_table", "canonical_id"):
        assert f'tgt."{key}" = staged."{key}"' in statement
        assert f'"{key}" = staged."{key}", ' not in statement
    assert 'UPDATE data_plane.canonical_lineage AS tgt SET' in statement
    assert "IS DISTINCT FROM" in statement


def test_refresh_sql_fails_closed_when_the_key_is_not_copied() -> None:
    relation = Relation("data_plane", "ingestion_runs", refresh_key=("run_id",))
    with pytest.raises(ForecastHistoryActivationError, match="refresh key is not copied"):
        refresh_sql(relation, ("status",), "activation_stage")
    with pytest.raises(ForecastHistoryActivationError, match="no columns to refresh"):
        refresh_sql(relation, ("run_id",), "activation_stage")


def test_prune_sql_only_drops_superseded_rows_that_keep_their_lineage() -> None:
    lineage = next(r for r in ACTIVATION_RELATIONS if r.table == "canonical_lineage")
    statement = prune_sql(lineage, "activation_stage")
    assert statement.startswith("DELETE FROM data_plane.canonical_lineage AS tgt")
    # Never leaves the declared source scope.
    assert "canonical_table = 'core.transactions'" in statement
    # Drops only what the source no longer selects...
    assert (
        'NOT EXISTS (SELECT 1 FROM "activation_stage" AS staged '
        'WHERE staged."run_id" = tgt."run_id" AND staged."canonical_id" = tgt."canonical_id")'
    ) in statement
    # ...and only while the source still carries lineage for that record.
    assert (
        'AND EXISTS (SELECT 1 FROM "activation_stage" AS keeper '
        'WHERE keeper."canonical_id" = tgt."canonical_id")'
    ) in statement
