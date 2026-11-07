from __future__ import annotations

import pytest

from shared.infrastructure.persistence.postgresql import PostgresEngine


def _engine(database) -> PostgresEngine:
    return PostgresEngine(
        database.url(),
        min_pool_size=1,
        max_pool_size=1,
        bootstrap=False,
        validate_schema=False,
    )


@pytest.mark.parametrize("access", ["engine_query", "raw_checkout"])
def test_server_terminated_idle_connection_is_replaced_before_checkout(
    intake_blank_db, access: str,
) -> None:
    engine = _engine(intake_blank_db)
    try:
        with engine.pooled_connection() as connection:
            old_pid = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]

        # End the server session after it has returned to the one-slot pool.
        # The client has not yet observed the closed socket; an is-closed flag
        # alone cannot protect the next borrower.
        with intake_blank_db.connect() as control:
            terminated = control.execute(
                "SELECT pg_terminate_backend(%s, 5000)", (old_pid,),
            ).fetchone()[0]
        assert terminated is True

        if access == "engine_query":
            row = engine.query_one("SELECT pg_backend_pid() AS pid, 7 AS value")
        else:
            with engine.pooled_connection() as connection:
                row = connection.execute(
                    "SELECT pg_backend_pid() AS pid, 7 AS value"
                ).fetchone()

        assert row is not None
        assert row["pid"] != old_pid
        assert row["value"] == 7
    finally:
        engine.close()


def test_connection_loss_inside_transaction_surfaces_and_rolls_back(
    intake_blank_db,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    engine = _engine(intake_blank_db)
    try:
        engine.execute("CREATE TABLE checkout_control (value integer NOT NULL)")

        with intake_blank_db.connect() as control:
            with pytest.raises(psycopg.OperationalError):
                with engine.lock:
                    engine.execute("INSERT INTO checkout_control VALUES (1)")
                    row = engine.query_one("SELECT pg_backend_pid() AS pid")
                    assert row is not None
                    assert control.execute(
                        "SELECT pg_terminate_backend(%s, 5000)", (row["pid"],),
                    ).fetchone()[0] is True
                    engine.execute("INSERT INTO checkout_control VALUES (2)")

        # Checkout recovery must not reconnect and replay an in-flight write.
        assert engine.query_one("SELECT count(*) AS count FROM checkout_control") == {
            "count": 0,
        }
    finally:
        engine.close()
