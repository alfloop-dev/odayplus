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

import json
import os
import uuid
from dataclasses import dataclass, field

import pytest

from shared.infrastructure.persistence import assisted_listing_intake as intake_migration

_ENV_DSN = "INTAKE_TEST_DATABASE_URL"


@pytest.fixture
def temp_env(tmp_path):
    """Provide isolated status/config/policy files for tooling tests."""
    status_file = tmp_path / "ai-status.json"
    config_file = tmp_path / "config.json"
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "required_status_checks": ["orchestrator", "product", "product-e2e-gate"],
                "enforce_admins": True,
                "required_approving_review_count": 1,
            }
        ),
        encoding="utf-8",
    )
    config_file.write_text(
        json.dumps(
            {
                "github_bus": {
                    "reviewers": {
                        "Codex": ["codex-bot", "codex-admin"],
                        "Claude": ["claude-bot"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    status_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "ODP-OC-R5-012",
                        "status": "review_approved",
                        "reviewer": "Codex",
                        "owner": "Antigravity",
                    },
                    {
                        "id": "ODP-OC-R5-011",
                        "status": "review",
                        "reviewer": "Claude",
                        "owner": "Claude",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return {"status": status_file, "config": config_file, "policy": policy_file}


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


def _install_uuid_ossp_stub(pgserver_module) -> None:
    """Provide ``uuid_generate_v4()`` offline, and nothing else.

    ``infra/db/migrations/000001_baseline_canonical_schema.sql`` and its
    successors declare ``CREATE EXTENSION IF NOT EXISTS "uuid-ossp"`` and then
    use ``uuid_generate_v4()`` as a column default. The bundled ``pgserver``
    build ships no contrib extensions, so those migrations could not apply here
    at all -- which is why ``tests/integration/test_assisted_listing_postgresql_runtime.py``
    was the one file in the live-marked set that could not run even locally.

    ``uuid_generate_v4()`` and the core ``gen_random_uuid()`` both return a
    random version-4 UUID, so aliasing one to the other preserves the semantics
    exactly rather than approximating them.

    The stub deliberately defines *only* v4. uuid-ossp also offers v1 (time and
    MAC derived), v3 and v5 (name derived) -- none of which a random generator
    can stand in for. Leaving them undefined means any future use fails with
    "function does not exist" rather than silently returning a random UUID where
    a deterministic one was meant. No migration uses them today; this keeps that
    true by construction.
    """
    from pathlib import Path

    install_root = Path(pgserver_module.__file__).resolve().parent / "pginstall"
    ext_dir = install_root / "share" / "postgresql" / "extension"
    if not ext_dir.is_dir():  # pragma: no cover - defensive
        return
    control = ext_dir / "uuid-ossp.control"
    if not control.exists():
        control.write_text(
            "comment = 'uuid-ossp stub (v4 only, aliased to core gen_random_uuid)'\n"
            "default_version = '1.1'\n"
            "relocatable = true\n",
            encoding="utf-8",
        )
    body = ext_dir / "uuid-ossp--1.1.sql"
    if not body.exists():
        body.write_text(
            "-- uuid-ossp stub: version 4 only.\n"
            "-- gen_random_uuid() is core in PostgreSQL 13+ and returns a v4 UUID,\n"
            "-- so this alias is exact. v1/v3/v5 are intentionally absent: a random\n"
            "-- generator cannot stand in for time- or name-derived UUIDs, and a\n"
            "-- missing function is a louder failure than a wrong value.\n"
            "CREATE FUNCTION uuid_generate_v4() RETURNS uuid\n"
            "    AS 'SELECT gen_random_uuid()'\n"
            "    LANGUAGE SQL VOLATILE;\n",
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
    import shutil
    import tempfile

    _install_pgcrypto_stub(pgserver)
    _install_uuid_ossp_stub(pgserver)
    data_dir = tempfile.mkdtemp(prefix="intake-pg16-")
    server = pgserver.get_server(data_dir)
    host = re.search(r"host=([^&]+)", server.get_uri()).group(1)
    admin = {"host": host, "dbname": "postgres", "user": "postgres"}
    try:
        yield IntakePgServer(psycopg=psycopg, admin_params=admin)
    finally:
        # `server.cleanup()` stops PostgreSQL but leaves the data directory it
        # was given. Each session therefore left ~54MB behind: a live host
        # accumulated 132 `intake-pg16-*` directories totalling 6.9G, none of
        # which any process still referenced.
        #
        # Removing it is also what keeps the leak bounded when this fixture
        # does NOT get to finish. A worker killed mid-run (supersede, lease
        # expiry) never reaches this block at all, and its pgserver -- which
        # calls setsid(), so no process group reaches it -- outlives the run.
        # Twelve such servers were still up after three days, and because the
        # worktree pruner refuses any tree a live process references, each one
        # also pinned its worktree against reclaim. Cleaning up on the paths we
        # DO control keeps that failure rare rather than routine.
        try:
            server.cleanup()
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


@dataclass
class IntakeDatabase:
    server: IntakePgServer
    dbname: str

    def connect(self, *, autocommit: bool = True, **overrides):
        return self.server.connect(self.dbname, autocommit=autocommit, **overrides)

    def url(self, *, driver: str = "") -> str:
        """Return this database's URL, for callers that need a URL not a socket.

        The fixtures hand out connection parameters, but anything driven through
        a URL -- ``PostgresEngine``, Alembic's ``sqlalchemy.url`` -- needs the
        assembled form, and the two differ only by the driver in the scheme.
        """

        from urllib.parse import quote

        params = self.server.admin_params
        scheme = f"postgresql+{driver}" if driver else "postgresql"
        user = quote(str(params.get("user") or "postgres"), safe="")
        password = params.get("password")
        credentials = (
            user if password is None else f"{user}:{quote(str(password), safe='')}"
        )
        host = str(params.get("host") or "localhost")
        if host.startswith("/"):
            # A Unix socket directory is a path, so it cannot sit in the netloc.
            # libpq takes it as a `host` query parameter instead, and the
            # pgserver fallback always hands out one of these.
            return (
                f"{scheme}://{credentials}@/{self.dbname}?host={quote(host, safe='')}"
            )
        port = params.get("port")
        netloc = host if port is None else f"{host}:{int(port)}"
        return f"{scheme}://{credentials}@{netloc}/{self.dbname}"

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
def reset_feature_flags():
    """Reset the global feature flag registry before each test.

    shared.auth.feature_flags.default_registry() returns a process-wide
    singleton so that enabling a flag through the admin API is visible to the
    authorization engine and the job queue. That shared truth is what the
    feature needs in production and what leaks between tests here: one test
    enabling a high-risk flag would otherwise leave it enabled for every test
    that runs after it in the same worker.
    """
    from shared.auth.feature_flags import reset_global_registry
    reset_global_registry()


@pytest.fixture(autouse=True)
def patch_synthetic_dns(request, monkeypatch):
    """Ensure any test DNS lookup for synthetic.example resolves successfully.

    Only applies to test modules related to assisted listing intake or retrieval to avoid global monkeypatching.
    """
    module_name = request.module.__name__
    if not any(k in module_name for k in ("assisted_listing", "intake", "retrieval", "snapshot")):
        return

    from modules.external_data.security import assisted_listing_retrieval
    original_resolve = assisted_listing_retrieval._resolve_host

    def mock_resolve(host: str):
        if "synthetic.example" in host:
            return ("93.184.216.34",)
        return original_resolve(host)

    monkeypatch.setattr(assisted_listing_retrieval, "_resolve_host", mock_resolve)
