"""Root test fixtures.

Currently provides real PostgreSQL 16 provisioning for the Assisted Listing
Intake schema contract and RLS security suites (ODP-INTAKE-SCHEMA-001).

Provisioning strategy (all imports are lazy so the minimal CI environment, which
has neither a database driver nor a Postgres binary, collects cleanly and simply
skips these live-environment tests):

1. If ``INTAKE_TEST_DATABASE_URL`` points at a reachable PostgreSQL 16 the suite
   creates a throwaway database inside that server.
2. Otherwise it provisions an ephemeral cluster from the ``pgserver`` package,
   which bundles PostgreSQL 16 binaries and needs no root. ``pgcrypto`` is stubbed
   there because ``gen_random_uuid()`` is a core function in PostgreSQL 13+, which
   is all the contract DDL actually relies on.
3. If neither is available the fixtures ``pytest.skip`` — the tests are marked
   ``requires_live_env`` and are excluded from the default CI marker expression.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

import pytest

from shared.infrastructure.persistence import assisted_listing_intake as intake_migration

_ENV_DSN = "INTAKE_TEST_DATABASE_URL"


def _install_pgcrypto_stub(pgserver_module) -> None:
    """Make ``CREATE EXTENSION IF NOT EXISTS pgcrypto`` a no-op offline.

    The bundled ``pgserver`` build ships no contrib extensions, but the intake
    DDL only needs ``gen_random_uuid()`` which is core in PostgreSQL 13+. Writing
    an empty extension definition lets the unmodified production DDL apply.
    """
    from pathlib import Path

    install_root = Path(pgserver_module.__file__).resolve().parent / "pginstall"
    ext_dir = install_root / "share" / "postgresql" / "extension"
    if not ext_dir.is_dir():  # pragma: no cover - defensive
        return
    control = ext_dir / "pgcrypto.control"
    if not control.exists():
        control.write_text(
            "comment = 'pgcrypto stub (gen_random_uuid is core in PG13+)'\n"
            "default_version = '1.3'\n"
            "relocatable = true\n",
            encoding="utf-8",
        )
    body = ext_dir / "pgcrypto--1.3.sql"
    if not body.exists():
        body.write_text(
            "-- no-op pgcrypto stub; gen_random_uuid() is core in PostgreSQL 13+\n",
            encoding="utf-8",
        )


@dataclass
class IntakePgServer:
    """A running PostgreSQL 16 admin endpoint that can mint scratch databases."""

    psycopg: object
    admin_params: dict[str, object]
    _created: list[str] = field(default_factory=list)

    def connect(self, dbname: str, *, autocommit: bool = True, **overrides):
        params = {**self.admin_params, "dbname": dbname, **overrides}
        return self.psycopg.connect(autocommit=autocommit, **params)

    def create_database(self) -> str:
        name = f"intake_test_{uuid.uuid4().hex[:12]}"
        with self.connect(self.admin_params["dbname"]) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        self._created.append(name)
        return name

    def drop_database(self, name: str) -> None:
        with self.connect(self.admin_params["dbname"]) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture(scope="session")
def intake_pg_server():
    psycopg = pytest.importorskip(
        "psycopg", reason="Assisted intake schema tests need the psycopg driver"
    )

    dsn = os.environ.get(_ENV_DSN)
    if dsn:
        admin = psycopg.conninfo.conninfo_to_dict(dsn)
        admin.setdefault("dbname", "postgres")
        try:
            psycopg.connect(autocommit=True, **admin).close()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"{_ENV_DSN} unreachable: {exc}")
        yield IntakePgServer(psycopg=psycopg, admin_params=admin)
        return

    pgserver = pytest.importorskip(
        "pgserver",
        reason="No INTAKE_TEST_DATABASE_URL and pgserver (bundled PostgreSQL 16) unavailable",
    )
    import re
    import tempfile

    _install_pgcrypto_stub(pgserver)
    data_dir = tempfile.mkdtemp(prefix="intake-pg16-")
    server = pgserver.get_server(data_dir)
    host = re.search(r"host=([^&]+)", server.get_uri()).group(1)
    admin = {"host": host, "dbname": "postgres", "user": "postgres"}
    try:
        yield IntakePgServer(psycopg=psycopg, admin_params=admin)
    finally:
        server.cleanup()


@dataclass
class IntakeDatabase:
    server: IntakePgServer
    dbname: str

    def connect(self, *, autocommit: bool = True, **overrides):
        return self.server.connect(self.dbname, autocommit=autocommit, **overrides)

    def apply_migration(self) -> None:
        with self.connect(autocommit=True) as conn:
            for _name, sql in intake_migration.upgrade_statements():
                conn.execute(sql)

    def apply_downgrade(self) -> None:
        with self.connect(autocommit=True) as conn:
            conn.execute(intake_migration.downgrade_sql())


@pytest.fixture
def intake_blank_db(intake_pg_server) -> IntakeDatabase:
    """A fresh empty PostgreSQL 16 database with no intake schema applied."""
    name = intake_pg_server.create_database()
    try:
        yield IntakeDatabase(server=intake_pg_server, dbname=name)
    finally:
        intake_pg_server.drop_database(name)


@pytest.fixture
def intake_db(intake_blank_db) -> IntakeDatabase:
    """A fresh PostgreSQL 16 database with the ordered intake migration applied."""
    intake_blank_db.apply_migration()
    return intake_blank_db


@pytest.fixture(autouse=True)
def reset_platform_metrics():
    """Reset the global default metrics registry before each test to prevent cross-test contamination."""
    from shared.observability.metrics import default_registry
    default_registry().clear()


@pytest.fixture(autouse=True)
def patch_synthetic_dns(monkeypatch):
    """Ensure any test DNS lookup for synthetic.example resolves successfully.

    This avoids hardcoding test-specific host shims in the production resolver.
    """
    from modules.external_data.security import assisted_listing_retrieval
    original_resolve = assisted_listing_retrieval._resolve_host

    def mock_resolve(host: str):
        if "synthetic.example" in host:
            return ("93.184.216.34",)
        return original_resolve(host)

    monkeypatch.setattr(assisted_listing_retrieval, "_resolve_host", mock_resolve)

