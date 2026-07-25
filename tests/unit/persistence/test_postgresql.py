from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from shared.infrastructure.persistence.postgresql import (
    PostgresEngine,
    PostgreSQLConfigurationError,
    PostgreSQLSchemaError,
    _convert_qmark_placeholders,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None, rowcount: int = 1) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    def __enter__(self) -> FakeTransaction:
        self._connection.transaction_depth += 1
        return self

    def __exit__(self, *_args: Any) -> None:
        self._connection.transaction_depth -= 1


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.transaction_depth = 0
        self.responses: list[list[dict[str, Any]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        self.statements.append((sql, params))
        rows = self.responses.pop(0) if self.responses else []
        return FakeCursor(rows)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.checkout_count = 0
        self.closed = False

    @contextmanager
    def connection(self):
        self.checkout_count += 1
        yield self._connection

    def close(self) -> None:
        self.closed = True


def make_engine(connection: FakeConnection | None = None) -> tuple[PostgresEngine, FakePool]:
    active_connection = connection or FakeConnection()
    pool = FakePool(active_connection)
    return (
        PostgresEngine(
            "postgresql://app:secret@db.example.test/oday",
            pool=pool,
            bootstrap=False,
            validate_schema=False,
        ),
        pool,
    )


def test_qmark_conversion_preserves_quoted_question_marks() -> None:
    assert (
        _convert_qmark_placeholders(
            "SELECT '?' AS literal, ? AS value, \"?\" AS identifier"
        )
        == "SELECT '?' AS literal, %s AS value, \"?\" AS identifier"
    )


def test_engine_uses_one_connection_for_transaction_bound_lock() -> None:
    engine, pool = make_engine()

    with engine.lock:
        engine.execute("INSERT INTO durable_sequences(name, counter) VALUES (?, ?)", ("a", 1))
        engine.query("SELECT counter FROM durable_sequences WHERE name = ?", ("a",))

    assert pool.checkout_count == 1
    executed = pool._connection.statements
    assert executed[0][0] == "SET LOCAL search_path TO odp_runtime, core, public"
    assert "%s" in executed[1][0]
    assert "%s" in executed[2][0]
    assert pool._connection.transaction_depth == 0


def test_query_normalizes_postgresql_native_values_for_existing_repositories() -> None:
    connection = FakeConnection()
    connection.responses.append([])
    connection.responses.append(
        [
            {
                "identifier": UUID("c13cf08f-1fc9-4d4a-a176-79833aff5a64"),
                "occurred_at": datetime(2026, 7, 24, 12, 30, tzinfo=UTC),
                "amount": Decimal("125.50"),
                "blob": memoryview(b"payload"),
                "payload": {"ready": True},
            }
        ]
    )
    engine, _ = make_engine(connection)

    row = engine.query_one("SELECT ? AS marker", ("x",))

    assert row == {
        "identifier": "c13cf08f-1fc9-4d4a-a176-79833aff5a64",
        "occurred_at": "2026-07-24T12:30:00+00:00",
        "amount": 125.5,
        "blob": b"payload",
        "payload": '{"ready":true}',
    }


def test_next_ordinal_uses_atomic_upsert_returning() -> None:
    connection = FakeConnection()
    connection.responses.extend([[], [{"counter": 7}]])
    engine, _ = make_engine(connection)

    assert engine.next_ordinal("documents:test") == 7
    assert "RETURNING counter" in connection.statements[-1][0]


def test_runtime_migration_is_idempotent_by_contract() -> None:
    connection = FakeConnection()
    engine, _ = make_engine(connection)

    engine.apply_runtime_migration()
    engine.apply_runtime_migration()

    migration_runs = [
        sql
        for sql, _params in connection.statements
        if "000008_postgresql_runtime_persistence" in sql
    ]
    assert len(migration_runs) == 2
    assert all("CREATE TABLE IF NOT EXISTS" in sql for sql in migration_runs)
    assert all("CREATE INDEX IF NOT EXISTS" in sql for sql in migration_runs)
    assert all("ON CONFLICT (migration_id) DO NOTHING" in sql for sql in migration_runs)


def test_schema_validation_fails_closed_when_canonical_relation_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _ = make_engine()

    def relation(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        del sql
        relation_name = str(params[0])
        return {
            "relation": None if relation_name == "core.transactions" else relation_name
        }

    monkeypatch.setattr(engine, "query_one", relation)

    with pytest.raises(PostgreSQLSchemaError, match="core.transactions"):
        engine.validate_schema()


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "sqlite:///tmp/oday.db",
        "https://db.example.test/oday",
        "postgresql://db.example.test",
    ],
)
def test_database_url_validation_fails_before_driver_or_network(
    database_url: str,
) -> None:
    with pytest.raises(PostgreSQLConfigurationError):
        PostgresEngine(
            database_url,
            pool=FakePool(FakeConnection()),
            bootstrap=False,
            validate_schema=False,
        )


def test_postgresql_runtime_migration_does_not_use_sqlite_ddl() -> None:
    migration = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "db"
        / "migrations"
        / "000008_postgresql_runtime_persistence.sql"
    ).read_text(encoding="utf-8")

    assert "AUTOINCREMENT" not in migration
    assert " BLOB " not in migration
    assert "BYTEA" in migration
    assert "JSONB" in migration
    assert "TIMESTAMPTZ" in migration
