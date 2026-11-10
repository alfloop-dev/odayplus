"""Native snapshot SQL must preserve its caller's transaction and failures."""

from contextlib import nullcontext

import pytest

from modules.external_data.application.source_snapshots import SourceSnapshotService


class NativePostgresConnection:
    autocommit = False

    def __init__(self, *, fetch_error=None, rows=None):
        self.commits = 0
        self.closed_cursors = 0
        self.fetch_error = fetch_error
        self.rows = rows if rows is not None else [("recorded",)]

    def commit(self):
        self.commits += 1

    def cursor(self):
        owner = self

        class Cursor:
            description = [("value",)]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                owner.closed_cursors += 1

            def execute(self, _sql, _params=()):
                pass

            def fetchall(self):
                if owner.fetch_error:
                    raise owner.fetch_error
                return owner.rows

        return Cursor()


def test_raw_snapshot_sql_never_commits_the_callers_transaction():
    conn = NativePostgresConnection()
    assert SourceSnapshotService(conn, object())._execute("SELECT value") == [{"value": "recorded"}]
    assert conn.commits == 0
    assert conn.closed_cursors == 1


def test_raw_snapshot_fetch_failure_is_not_reported_as_an_empty_success():
    conn = NativePostgresConnection(fetch_error=OSError("synthetic fetch failure"))
    with pytest.raises(OSError, match="synthetic fetch failure"):
        SourceSnapshotService(conn, object())._execute("SELECT value")
    assert conn.commits == 0
    assert conn.closed_cursors == 1


def test_native_dict_rows_keep_values_instead_of_column_names():
    conn = NativePostgresConnection(rows=[{"value": "recorded"}])
    assert SourceSnapshotService(conn, object())._execute("SELECT value") == [{"value": "recorded"}]


def test_snapshot_sql_uses_the_runtime_postgres_engine_interface():
    class NativeEngine:
        dialect = "postgresql"

        def transaction(self):
            return nullcontext(self)

        def query(self, sql, params):
            assert params == ("recorded",)
            return [{"value": "recorded"}]

    assert SourceSnapshotService(NativeEngine(), object())._execute(
        "SELECT %s AS value", ("recorded",),
    ) == [{"value": "recorded"}]
