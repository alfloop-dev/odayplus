"""Delete / tombstone propagation in the data-plane landing layer.

Scope limit, stated so these results are not over-read. Everything here is
offline: synthetic envelopes, a disposable PostgreSQL 16 cluster provisioned by
``pgserver``, and a minimal stand-in for the canonical entity schema that is
faithful only in the keys ``sql/control_schema.sql`` actually references. No
upstream change stream is opened, no credential is read, and no live row is
deleted. Passing here says the offline delete semantics hold; it says nothing
about the Phase 34B CDC adapter, its live latency, or the H07 source decision.

The suite is three layers, because the guarantees live in three places:

* pure decision semantics (ordering, idempotency, tenant binding),
* the SQL each decision emits (predicates and the version guard),
* and an end-to-end run on a real PostgreSQL server, which is the only layer
  that can prove a row is actually gone and stays gone across a restart.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from psycopg import sql

from apps.data_platform.contracts import QuarantineReason, SourceKind
from apps.data_platform.deletion import (
    DeleteEvent,
    DeleteOutcome,
    DeletePropagationMode,
    DeleteScope,
    RETAINED_CANONICAL_TABLES,
    decide_delete,
    envelope_version,
    plan_purge,
    purgeable_tables,
    resolve_delete_tenant,
    suppresses_upsert,
    version_from_timestamp,
)
from apps.data_platform.serialization import aggregate_checksum
from apps.data_platform.source import envelope_for_document
from apps.data_platform.store import PsycopgCanonicalStore

CONTROL_SCHEMA = "data_plane"
OBSERVED_AT = datetime(2026, 7, 24, 12, tzinfo=UTC)
TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")


def _schema_sql() -> str:
    return (
        Path(__file__).parents[1].joinpath("sql", "control_schema.sql").read_text(encoding="utf-8")
    )


def _version(moment: datetime) -> int:
    return version_from_timestamp(moment)


# ---------------------------------------------------------------------------
# 1. Decision semantics: ordering, idempotency and tenant binding
# ---------------------------------------------------------------------------


def test_replayed_delete_is_idempotent_and_a_late_older_delete_never_regresses() -> None:
    recorded = _version(datetime(2026, 7, 20, tzinfo=UTC))
    newer = _version(datetime(2026, 7, 21, tzinfo=UTC))
    older = _version(datetime(2026, 7, 19, tzinfo=UTC))

    replay = decide_delete(
        requested_version=recorded, recorded_version=recorded, downstream_target_count=1
    )
    late = decide_delete(
        requested_version=older, recorded_version=recorded, downstream_target_count=1
    )
    advance = decide_delete(
        requested_version=newer, recorded_version=recorded, downstream_target_count=1
    )

    assert replay.outcome is DeleteOutcome.REPLAYED
    # A replay still converges the sink rather than being skipped outright, so a
    # partially applied earlier attempt cannot leave rows behind forever.
    assert replay.records_tombstone and replay.purges_rows
    assert late.outcome is DeleteOutcome.STALE_IGNORED
    assert not late.records_tombstone and not late.purges_rows
    assert advance.outcome is DeleteOutcome.APPLIED


def test_delete_without_an_orderable_source_version_is_refused() -> None:
    for requested in (None, -1):
        decision = decide_delete(
            requested_version=requested,
            recorded_version=None,
            downstream_target_count=3,
        )
        assert decision.outcome is DeleteOutcome.REJECTED_UNKNOWN_VERSION
        assert not decision.records_tombstone
        assert not decision.purges_rows


def test_missing_downstream_data_still_records_a_tombstone() -> None:
    decision = decide_delete(
        requested_version=_version(OBSERVED_AT),
        recorded_version=None,
        downstream_target_count=0,
    )

    assert decision.outcome is DeleteOutcome.ABSENT_TOMBSTONED
    # Nothing to purge, but the tombstone must exist or a later replay of the
    # pre-delete document would land the entity after it was deleted.
    assert decision.records_tombstone
    assert not decision.purges_rows


def test_a_delete_is_bound_to_exactly_one_owning_tenant() -> None:
    declared_owner = resolve_delete_tenant(TENANT_A, [TENANT_A])
    foreign = resolve_delete_tenant(TENANT_B, [TENANT_A])
    inferred = resolve_delete_tenant(None, [TENANT_A, TENANT_A])
    ambiguous = resolve_delete_tenant(None, [TENANT_A, TENANT_B])
    orphan = resolve_delete_tenant(None, [])

    assert declared_owner.resolved and declared_owner.tenant_id == TENANT_A
    assert foreign.outcome is DeleteOutcome.REJECTED_TENANT_BOUNDARY
    assert foreign.tenant_id is None
    assert inferred.resolved and inferred.tenant_id == TENANT_A
    assert ambiguous.outcome is DeleteOutcome.REJECTED_AMBIGUOUS_TENANT
    assert orphan.outcome is DeleteOutcome.REJECTED_UNRESOLVED_TENANT


def test_an_unknown_or_older_version_can_never_resurrect_a_deleted_entity() -> None:
    deleted_at = _version(datetime(2026, 7, 20, tzinfo=UTC))

    assert suppresses_upsert(deleted_at, None) is True
    assert suppresses_upsert(deleted_at, deleted_at - 1) is True
    assert suppresses_upsert(deleted_at, deleted_at) is True
    assert suppresses_upsert(deleted_at, deleted_at + 1) is False
    assert suppresses_upsert(None, None) is False


def test_envelope_version_ignores_ingestion_time() -> None:
    dated = envelope_for_document(
        SourceKind.CAMPAIGN,
        {"_id": "campaign-1", "merchant": "merchant-1", "offerName": "A",
         "updatedAt": "2026-07-20T00:00:00Z"},
        run_id=str(uuid.uuid4()),
        observed_at=OBSERVED_AT,
    )
    undated = envelope_for_document(
        SourceKind.PRODUCT,
        {"_id": "product-1", "merchant": "merchant-1", "title": "T"},
        run_id=str(uuid.uuid4()),
        observed_at=OBSERVED_AT,
    )

    assert envelope_version(dated) == _version(datetime(2026, 7, 20, tzinfo=UTC))
    # observed_at is when this pipeline read the record, not when the source
    # changed; using it would make any replay look newer than the delete.
    assert envelope_version(undated) is None


def test_purge_plan_scopes_every_statement_and_retains_identity_tables() -> None:
    snapshot = UUID("00000000-0000-4000-8000-000000000101")
    store_id = UUID("00000000-0000-4000-8000-000000000102")
    plan = plan_purge(
        [
            (f"{CONTROL_SCHEMA}.domain_inputs", snapshot),
            ("core.stores", store_id),
            ("public.some_unregistered_table", store_id),
        ],
        tenant_id=TENANT_A,
        control_schema=CONTROL_SCHEMA,
    )

    assert len(plan.statements) == 1
    statement, params = plan.statements[0]
    assert "tenant_id = %s" in statement
    assert params == (snapshot, TENANT_A)
    # Identity tables and any table not in the registry are reported, never
    # turned into DELETE SQL built from a name that came out of the database.
    assert plan.retained_targets == ("core.stores", "public.some_unregistered_table")
    assert RETAINED_CANONICAL_TABLES.isdisjoint(purgeable_tables(CONTROL_SCHEMA))


def test_every_purge_statement_carries_a_tenant_predicate() -> None:
    targets = [
        (table, UUID("00000000-0000-4000-8000-000000000103"))
        for table in sorted(purgeable_tables(CONTROL_SCHEMA))
    ]
    plan = plan_purge(targets, tenant_id=TENANT_A, control_schema=CONTROL_SCHEMA)

    assert plan.retained_targets == ()
    assert plan.statements
    for statement, params in plan.statements:
        assert statement.startswith("DELETE FROM ")
        assert statement.count("%s") == 2
        assert params[1] == TENANT_A
        assert "tenant_id = %s" in statement


# ---------------------------------------------------------------------------
# 2. SQL contract: the guards the decisions are compiled into
# ---------------------------------------------------------------------------


def test_control_schema_declares_the_tombstone_audit_table() -> None:
    schema = _schema_sql()

    assert "CREATE TABLE IF NOT EXISTS {{control_schema}}.tombstones" in schema
    assert "PRIMARY KEY (tenant_id, entity_type, entity_id)" in schema
    assert "source_version BIGINT NOT NULL CHECK (source_version >= 0)" in schema
    assert "tombstone_hash TEXT NOT NULL CHECK (length(tombstone_hash) = 64)" in schema
    assert "propagation_mode IN ('TOMBSTONE_PURGE', 'SINK_DELETE')" in schema
    assert "retained_targets TEXT[] NOT NULL DEFAULT '{}'" in schema
    assert "ix_data_plane_tombstones_entity" in schema
    assert "tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id)" in schema


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _ScriptedConnection:
    """A connection that answers by SQL fragment and records what it was asked."""

    def __init__(self, responses: list[tuple[str, _Cursor]]) -> None:
        self._responses = responses
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> _ScriptedConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def transaction(self) -> _ScriptedConnection:
        return self

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        assert sql.count("%s") == len(params), sql
        self.statements.append((sql, params))
        for fragment, cursor in self._responses:
            if fragment in sql:
                return cursor
        return _Cursor([])

    def sql_containing(self, fragment: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [entry for entry in self.statements if fragment in entry[0]]


def _store(connection: _ScriptedConnection) -> PsycopgCanonicalStore:
    store = object.__new__(PsycopgCanonicalStore)
    store._config = SimpleNamespace(
        control_schema=CONTROL_SCHEMA,
        raw_schema="fongniao_raw",
    )
    store._status_contract = None
    store._connect = lambda: connection
    return store


def _delete_event(
    *,
    tenant_id: UUID | None = TENANT_A,
    source_version: int | None = None,
    source_id: str = "campaign-1",
) -> DeleteEvent:
    return DeleteEvent(
        scope=DeleteScope(SourceKind.CAMPAIGN, source_id, tenant_id),
        source_version=source_version or _version(datetime(2026, 7, 22, tzinfo=UTC)),
        purged_at=datetime(2026, 7, 22, tzinfo=UTC),
        source_snapshot_id="00000000-0000-4000-8000-0000000000f1",
        tombstone_hash="d" * 64,
        run_id="00000000-0000-4000-8000-0000000000f2",
        context={"reason": "upstream_delete"},
    )


def test_sink_delete_emits_only_tenant_scoped_statements_and_a_version_guard() -> None:
    snapshot = UUID("00000000-0000-4000-8000-000000000201")
    store_id = UUID("00000000-0000-4000-8000-000000000202")
    connection = _ScriptedConnection(
        [
            (
                "FROM data_plane.canonical_lineage",
                _Cursor(
                    [
                        (TENANT_A, f"{CONTROL_SCHEMA}.domain_inputs", snapshot),
                        (TENANT_A, "core.stores", store_id),
                        (TENANT_B, f"{CONTROL_SCHEMA}.domain_inputs", snapshot),
                    ]
                ),
            ),
            ("FROM core.tenants", _Cursor([(1,)])),
            ("SELECT source_version, purged_at", _Cursor([])),
            ("DELETE FROM data_plane.domain_inputs", _Cursor([], rowcount=1)),
            ("INSERT INTO data_plane.tombstones", _Cursor([(7, 0, 1)])),
        ]
    )

    result = _store(connection).delete_record(_delete_event())

    assert result.outcome is DeleteOutcome.APPLIED
    assert result.purged_row_count == 1
    assert result.retained_targets == ("core.stores",)
    deletes = connection.sql_containing("DELETE FROM ")
    assert len(deletes) == 1, "the other tenant's lineage row must not be purged"
    assert deletes[0][1] == (snapshot, TENANT_A)
    upsert = connection.sql_containing("INSERT INTO data_plane.tombstones")[0][0]
    assert (
        "WHERE EXCLUDED.source_version >= data_plane.tombstones.source_version" in upsert
    )
    assert "replay_count = data_plane.tombstones.replay_count + 1" in upsert


def test_tombstone_only_mode_records_evidence_without_deleting_rows() -> None:
    snapshot = UUID("00000000-0000-4000-8000-000000000203")
    connection = _ScriptedConnection(
        [
            (
                "FROM data_plane.canonical_lineage",
                _Cursor([(TENANT_A, f"{CONTROL_SCHEMA}.domain_inputs", snapshot)]),
            ),
            ("FROM core.tenants", _Cursor([(1,)])),
            ("SELECT source_version, purged_at", _Cursor([])),
            ("INSERT INTO data_plane.tombstones", _Cursor([(7, 0, 0)])),
        ]
    )

    result = _store(connection).tombstone_record(_delete_event())

    assert result.outcome is DeleteOutcome.APPLIED
    assert connection.sql_containing("DELETE FROM ") == []
    assert result.purged_row_count == 0
    assert result.retained_targets == (f"{CONTROL_SCHEMA}.domain_inputs",)
    assert result.as_dict()["propagation_mode"] == DeletePropagationMode.TOMBSTONE_PURGE.value


def test_a_delete_naming_a_foreign_tenant_touches_nothing() -> None:
    snapshot = UUID("00000000-0000-4000-8000-000000000204")
    connection = _ScriptedConnection(
        [
            (
                "FROM data_plane.canonical_lineage",
                _Cursor([(TENANT_A, f"{CONTROL_SCHEMA}.domain_inputs", snapshot)]),
            ),
        ]
    )

    result = _store(connection).delete_record(_delete_event(tenant_id=TENANT_B))

    assert result.outcome is DeleteOutcome.REJECTED_TENANT_BOUNDARY
    assert result.rejected
    assert connection.sql_containing("DELETE FROM ") == []
    assert connection.sql_containing("INSERT INTO data_plane.tombstones") == []


def test_an_unknown_tenant_is_refused_before_any_write() -> None:
    connection = _ScriptedConnection(
        [
            ("FROM data_plane.canonical_lineage", _Cursor([])),
            ("FROM core.tenants", _Cursor([])),
        ]
    )

    result = _store(connection).delete_record(_delete_event())

    assert result.outcome is DeleteOutcome.REJECTED_UNRESOLVED_TENANT
    assert connection.sql_containing("DELETE FROM ") == []
    assert connection.sql_containing("INSERT INTO data_plane.tombstones") == []


def test_batch_projection_quarantines_a_record_a_newer_delete_already_removed() -> None:
    deleted_version = _version(datetime(2026, 7, 22, tzinfo=UTC))
    connection = _ScriptedConnection(
        [("FROM data_plane.tombstones", _Cursor([("campaign-1", deleted_version)]))]
    )
    envelope = envelope_for_document(
        SourceKind.CAMPAIGN,
        {
            "_id": "campaign-1",
            "merchant": "merchant-1",
            "offerName": "Stale offer",
            "updatedAt": "2026-07-20T00:00:00Z",
        },
        run_id="00000000-0000-4000-8000-0000000000f2",
        observed_at=OBSERVED_AT,
    )

    result = _store(connection).apply_batch(
        SourceKind.CAMPAIGN, [envelope], partition_key="2026-07-23"
    )

    assert result.valid_loaded == 0
    assert result.quarantine_reason_counts == {QuarantineReason.SOURCE_DELETED.value: 1}
    quarantine = connection.sql_containing("INSERT INTO data_plane.quarantined_records")
    assert len(quarantine) == 1
    assert QuarantineReason.SOURCE_DELETED.value in quarantine[0][1]
    assert connection.sql_containing("INSERT INTO data_plane.domain_inputs") == []


def test_reconcile_reports_sink_delete_drift() -> None:
    connection = _ScriptedConnection(
        [
            ("to_regclass", _Cursor([(None,)])),
            ("FROM data_plane.tombstones AS tomb", _Cursor([(2,)])),
        ]
    )
    empty = aggregate_checksum([])

    result = _store(connection).reconcile(
        "00000000-0000-4000-8000-0000000000f3", SourceKind.CAMPAIGN, 0, empty, empty
    )

    assert result.sink_delete_drift == 2
    # Drift is a reconciliation failure: an entity upstream deleted came back.
    assert not result.reconciled


# ---------------------------------------------------------------------------
# 3. End-to-end on a disposable PostgreSQL 16 server
# ---------------------------------------------------------------------------

# Faithful only in the keys sql/control_schema.sql references. The production
# canonical schema (infra/db/migrations) carries far more columns, constraints
# and PostGIS types that the bundled pgserver build cannot host.
_MINIMAL_CORE_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE core.tenants (
    tenant_id UUID PRIMARY KEY,
    tenant_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE core.brands (
    brand_id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES core.tenants(tenant_id),
    brand_code TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    brand_type TEXT NOT NULL,
    brand_capture_group TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, brand_id)
);
CREATE TABLE core.stores (
    store_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    brand_id UUID REFERENCES core.brands(brand_id),
    source_store_id TEXT
);
CREATE TABLE core.machines (
    machine_id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    source_machine_id TEXT
);
CREATE TABLE core.transactions (
    transaction_id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID REFERENCES core.machines(machine_id)
);
CREATE TABLE core.machine_status_events (
    status_event_id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    event_time TIMESTAMPTZ NOT NULL
);
"""


@pytest.fixture(scope="session")
def _pg_admin_params():
    psycopg = pytest.importorskip(
        "psycopg", reason="delete propagation end-to-end needs the psycopg driver"
    )
    dsn = os.environ.get("INTAKE_TEST_DATABASE_URL")
    if dsn:
        admin = psycopg.conninfo.conninfo_to_dict(dsn)
        admin.setdefault("dbname", "postgres")
        try:
            psycopg.connect(autocommit=True, **admin).close()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"INTAKE_TEST_DATABASE_URL unreachable: {exc}")
        yield psycopg, admin
        return

    pgserver = pytest.importorskip(
        "pgserver",
        reason="No INTAKE_TEST_DATABASE_URL and pgserver (bundled PostgreSQL 16) unavailable",
    )
    data_dir = tempfile.mkdtemp(prefix="data-plane-delete-pg16-")
    server = pgserver.get_server(data_dir)
    try:
        host = re.search(r"host=([^&]+)", server.get_uri()).group(1)
        yield psycopg, {"host": host, "dbname": "postgres", "user": "postgres"}
    finally:
        # pgserver leaves its data directory behind and calls setsid(), so an
        # abandoned server outlives the run and pins this worktree against
        # reclaim. Clean up the paths this fixture does control.
        try:
            server.cleanup()
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
def live_store(_pg_admin_params):
    """A canonical store over a throwaway database with the real control DDL."""
    psycopg, admin = _pg_admin_params
    dbname = f"dp_delete_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(autocommit=True, **admin) as admin_connection:
        admin_connection.execute(f'CREATE DATABASE "{dbname}"')
    params = {**admin, "dbname": dbname}

    def connect():
        return psycopg.connect(**params)

    def build_store() -> PsycopgCanonicalStore:
        store = object.__new__(PsycopgCanonicalStore)
        store._config = SimpleNamespace(
            control_schema=CONTROL_SCHEMA,
            raw_schema="fongniao_raw",
        )
        store._status_contract = None
        store._connect = connect
        return store

    try:
        with connect() as connection:
            connection.execute(_MINIMAL_CORE_SCHEMA)
        build_store().install()
        yield SimpleNamespace(build=build_store, connect=connect, store=build_store())
    finally:
        with psycopg.connect(autocommit=True, **admin) as admin_connection:
            admin_connection.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _merchant_document(source_id: str) -> dict[str, Any]:
    return {
        "_id": source_id,
        "companyName": f"Merchant {source_id}",
        "country": "TW",
        "currency": "TWD",
        "operation": "active",
        "createdAt": "2026-07-01T00:00:00Z",
    }


def _campaign_document(source_id: str, merchant: str, updated_at: datetime) -> dict[str, Any]:
    return {
        "_id": source_id,
        "merchant": merchant,
        "offerName": f"Offer {source_id}",
        "isActive": True,
        "startDatetime": "2026-07-20T00:00:00Z",
        "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
    }


def _land(store: PsycopgCanonicalStore, kind: SourceKind, document: dict[str, Any]):
    run_id = str(uuid.uuid4())
    store.begin_run(run_id, kind, "2026-07-23", None, OBSERVED_AT)
    envelope = envelope_for_document(kind, document, run_id=run_id, observed_at=OBSERVED_AT)
    return run_id, envelope, store.apply_batch(kind, [envelope], partition_key="2026-07-23")


def _domain_input_rows(connect, source_id: str) -> list[tuple[Any, ...]]:
    with connect() as connection:
        query = sql.SQL(
            "SELECT tenant_id, source_id FROM {schema}.domain_inputs "
            "WHERE source_id = %s"
        ).format(schema=sql.Identifier(CONTROL_SCHEMA))
        return connection.execute(
            query,
            (source_id,),
        ).fetchall()


def _seed_two_tenants(live_store) -> tuple[UUID, UUID]:
    store = live_store.store
    for merchant in ("merchant-a", "merchant-b"):
        _, _, result = _land(store, SourceKind.MERCHANT, _merchant_document(merchant))
        assert result.valid_loaded == 1, result.quarantine_reason_counts
    _, _, campaign_a = _land(
        store,
        SourceKind.CAMPAIGN,
        _campaign_document("campaign-a", "merchant-a", datetime(2026, 7, 21, tzinfo=UTC)),
    )
    _, _, campaign_b = _land(
        store,
        SourceKind.CAMPAIGN,
        _campaign_document("campaign-b", "merchant-b", datetime(2026, 7, 21, tzinfo=UTC)),
    )
    assert campaign_a.valid_loaded == 1, campaign_a.quarantine_reason_counts
    assert campaign_b.valid_loaded == 1, campaign_b.quarantine_reason_counts
    from apps.data_platform.identifiers import tenant_id_for_merchant

    return tenant_id_for_merchant("merchant-a"), tenant_id_for_merchant("merchant-b")


def _event_for(
    tenant_id: UUID | None,
    source_id: str,
    moment: datetime,
    run_id: str,
) -> DeleteEvent:
    return DeleteEvent(
        scope=DeleteScope(SourceKind.CAMPAIGN, source_id, tenant_id),
        source_version=_version(moment),
        purged_at=moment,
        source_snapshot_id=str(uuid.uuid4()),
        tombstone_hash=f"{abs(hash((source_id, moment))):064x}"[:64],
        run_id=run_id,
        context={"reason": "upstream_delete", "source_id": source_id},
    )


@pytest.mark.requires_live_env
def test_sink_delete_removes_only_the_owning_tenants_rows(live_store) -> None:
    tenant_a, tenant_b = _seed_two_tenants(live_store)
    store = live_store.store
    run_id = str(uuid.uuid4())
    store.begin_run(run_id, SourceKind.CAMPAIGN, "2026-07-23", None, OBSERVED_AT)

    foreign = store.delete_record(
        _event_for(tenant_b, "campaign-a", datetime(2026, 7, 22, tzinfo=UTC), run_id)
    )
    assert foreign.outcome is DeleteOutcome.REJECTED_TENANT_BOUNDARY
    assert _domain_input_rows(live_store.connect, "campaign-a")

    applied = store.delete_record(
        _event_for(tenant_a, "campaign-a", datetime(2026, 7, 22, tzinfo=UTC), run_id)
    )

    assert applied.outcome is DeleteOutcome.APPLIED
    assert applied.purged_row_count == 1
    assert _domain_input_rows(live_store.connect, "campaign-a") == []
    # The other tenant's identically-shaped record is untouched.
    assert len(_domain_input_rows(live_store.connect, "campaign-b")) == 1
    tombstones = live_store.connect().execute(
        sql.SQL("SELECT entity_id FROM {schema}.tombstones").format(
            schema=sql.Identifier(CONTROL_SCHEMA)
        )
    ).fetchall()
    assert [row[0] for row in tombstones] == ["campaign-a"]


@pytest.mark.requires_live_env
def test_replay_and_a_late_older_delete_converge_on_one_state(live_store) -> None:
    tenant_a, _ = _seed_two_tenants(live_store)
    store = live_store.store
    run_id = str(uuid.uuid4())
    store.begin_run(run_id, SourceKind.CAMPAIGN, "2026-07-23", None, OBSERVED_AT)
    winning = datetime(2026, 7, 22, tzinfo=UTC)

    first = store.delete_record(_event_for(tenant_a, "campaign-a", winning, run_id))
    replay = store.delete_record(_event_for(tenant_a, "campaign-a", winning, run_id))
    late = store.delete_record(
        _event_for(tenant_a, "campaign-a", winning - timedelta(days=1), run_id)
    )
    absent = store.delete_record(
        _event_for(tenant_a, "campaign-never-landed", winning, run_id)
    )

    assert first.outcome is DeleteOutcome.APPLIED
    assert replay.outcome is DeleteOutcome.REPLAYED
    assert late.outcome is DeleteOutcome.STALE_IGNORED
    assert absent.outcome is DeleteOutcome.ABSENT_TOMBSTONED
    tombstone = store.get_tombstone(tenant_a, SourceKind.CAMPAIGN, "campaign-a")
    assert tombstone is not None
    assert tombstone.source_version == _version(winning)
    assert tombstone.replay_count == 1
    assert tombstone.purged_row_count == 1, "a replay must not double-count a purge"
    assert _domain_input_rows(live_store.connect, "campaign-a") == []


@pytest.mark.requires_live_env
def test_an_older_upsert_after_a_delete_is_quarantined_not_resurrected(live_store) -> None:
    tenant_a, _ = _seed_two_tenants(live_store)
    store = live_store.store
    run_id = str(uuid.uuid4())
    store.begin_run(run_id, SourceKind.CAMPAIGN, "2026-07-23", None, OBSERVED_AT)
    store.delete_record(
        _event_for(tenant_a, "campaign-a", datetime(2026, 7, 22, tzinfo=UTC), run_id)
    )

    # A fresh store instance stands in for a restart: nothing is carried in
    # memory, so the guard has to come back off disk.
    restarted = live_store.build()
    assert restarted.get_tombstone(tenant_a, SourceKind.CAMPAIGN, "campaign-a") is not None

    _, _, stale = _land(
        restarted,
        SourceKind.CAMPAIGN,
        _campaign_document("campaign-a", "merchant-a", datetime(2026, 7, 21, tzinfo=UTC)),
    )
    assert stale.quarantine_reason_counts == {QuarantineReason.SOURCE_DELETED.value: 1}
    assert _domain_input_rows(live_store.connect, "campaign-a") == []

    _, _, newer = _land(
        restarted,
        SourceKind.CAMPAIGN,
        _campaign_document("campaign-a", "merchant-a", datetime(2026, 7, 23, tzinfo=UTC)),
    )
    # A genuinely newer source version is a legitimate re-creation upstream and
    # is allowed through; only stale and unordered records are blocked.
    assert newer.valid_loaded == 1, newer.quarantine_reason_counts
    assert len(_domain_input_rows(live_store.connect, "campaign-a")) == 1


@pytest.mark.requires_live_env
def test_reconcile_flags_a_reprojection_that_bypassed_the_delete_guard(live_store) -> None:
    tenant_a, _ = _seed_two_tenants(live_store)
    store = live_store.store
    run_id = str(uuid.uuid4())
    store.begin_run(run_id, SourceKind.CAMPAIGN, "2026-07-23", None, OBSERVED_AT)
    store.delete_record(
        _event_for(tenant_a, "campaign-a", datetime(2026, 7, 22, tzinfo=UTC), run_id)
    )
    empty = aggregate_checksum([])

    clean = store.reconcile(run_id, SourceKind.CAMPAIGN, 0, empty, empty)

    # Simulate a writer that landed the entity again without consulting the
    # tombstone; the drift detector must not depend on that writer cooperating.
    with live_store.connect() as connection:
        query = sql.SQL(
            """
            INSERT INTO {schema}.canonical_lineage (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, tenant_id, canonical_table, canonical_id, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP + interval '1 hour')
            """
        ).format(schema=sql.Identifier(CONTROL_SCHEMA))
        connection.execute(
            query,
            (
                str(uuid.uuid4()),
                SourceKind.CAMPAIGN.value,
                "campaign-a",
                "e" * 64,
                run_id,
                tenant_a,
                f"{CONTROL_SCHEMA}.domain_inputs",
                str(uuid.uuid4()),
            ),
        )

    drifted = store.reconcile(run_id, SourceKind.CAMPAIGN, 0, empty, empty)

    assert clean.sink_delete_drift == 0
    assert drifted.sink_delete_drift == 1
    assert not drifted.reconciled
