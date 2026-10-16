"""F1/F9: real repository monotonicity, atomic rotation and honest storage failure.

Separate owning controls; not additions to the original 2178/178 selections.
PostgreSQL is mandatory: supplied isolated DSN or an owned bundled real server.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import signal
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from queue import Queue
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from shared.identity.session_service import (
    InMemorySessionRepository,
    SessionRotationRefused,
    SessionService,
    SessionStorageUnavailable,
)
from shared.identity.session_store import SqlSessionRepository


@pytest.fixture(scope="module")
def database_url(record_testsuite_property):
    url = os.environ.get("INTAKE_TEST_DATABASE_URL")
    server = None
    owned = None
    log = None
    initialized = False
    try:
        if not url:
            # Use only the declared pgserver distribution's actual binaries.
            # Its get_server constructor has an unbounded readiness loop and a
            # daemon/PID-file cleanup fallback. Own a foreground Popen instead:
            # no shared server discovery, stale PID signalling or extension stubs.
            import pgserver

            binary = Path(pgserver.__file__).resolve().parent / "pginstall/bin"
            owned = Path(tempfile.mkdtemp(prefix="session-real-pg-"))
            data, sockets = owned / "data", owned / "sockets"
            sockets.mkdir(mode=0o700)
            record_testsuite_property("session_pg_source", "owned-pgserver-binary")
            record_testsuite_property("session_pg_package_version", version("pgserver"))
            record_testsuite_property("session_pg_binary", str(binary / "postgres"))
            record_testsuite_property("session_pg_owned_directory", str(owned))
            log = (owned / "server.log").open("w+")
            subprocess.run(
                [str(binary / "initdb"), "-D", str(data), "-A", "trust",
                 "-U", "postgres", "--encoding=UTF8", "--no-locale"],
                check=True, stdout=log, stderr=subprocess.STDOUT, timeout=45,
            )
            initialized = True
            server = subprocess.Popen(
                [str(binary / "postgres"), "-D", str(data), "-h", "", "-k", str(sockets)],
                stdout=log, stderr=subprocess.STDOUT,
            )
            record_testsuite_property("session_pg_owned_pid", server.pid)
            record_testsuite_property(
                "session_pg_kernel_start_ticks",
                Path(f"/proc/{server.pid}/stat").read_text().rsplit(")", 1)[1].split()[19],
            )
            url = f"postgresql://postgres@/postgres?host={sockets}"
            deadline = time.monotonic() + 45
            while True:
                assert server.poll() is None, "owned PostgreSQL exited before readiness"
                try:
                    with psycopg.connect(url, connect_timeout=2):
                        break
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("mandatory owned real PostgreSQL unavailable") from None
                    time.sleep(.05)
        else:
            record_testsuite_property("session_pg_source", "supplied-isolated-dsn")
        with psycopg.connect(url, connect_timeout=2) as conn:
            server_version = conn.execute("SELECT version()").fetchone()[0]
            assert "PostgreSQL" in server_version
            record_testsuite_property("session_pg_version", server_version)
            conn.execute((Path(__file__).resolve().parents[2] /
                          "infra/db/migrations/000011_identity_schema.sql").read_text())
        # The original persisted-expiry controls also open independent clients
        # using this variable. Bind ONLY this module's owned fixture lifetime.
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("INTAKE_TEST_DATABASE_URL", url)
            yield url
    finally:
        stopped = server is None and initialized
        if server is not None:
            if server.poll() is None:
                server.send_signal(signal.SIGINT)  # fast shutdown of this Popen only
            try:
                code = server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                code = server.wait(timeout=5)
            stopped = code == 0
            record_testsuite_property("session_pg_owned_wait_exit", code)
        if log is not None:
            log.flush()
            log.seek(0)
            print(log.read())
            log.close()
        # Forced/uncertain closure or interrupted initialization retains data
        # for the owning coordinator, never deleting files beneath unknown writers.
        if owned is not None and stopped:
            shutil.rmtree(owned)
        if server is not None:
            assert stopped, "owned server did not shut down cleanly; data retained"


@pytest.fixture
def account(database_url):
    account_id, tenant_id = uuid4(), uuid4()
    with psycopg.connect(database_url) as conn:
        conn.execute(
            """INSERT INTO identity.accounts
               (account_id, tenant_id, username, email, status, created_by)
               VALUES (%s, %s, %s, %s, 'active', 'offline-session-contract')""",
            (account_id, tenant_id, str(account_id), f"{account_id}@example.invalid"),
        )
    yield account_id
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM identity.accounts WHERE account_id = %s", (account_id,))


@pytest.fixture(params=[False, True], ids=["tuple-rows", "dict-rows"])
def repository(database_url, request):
    return SqlSessionRepository(lambda: psycopg.connect(
        database_url, row_factory=dict_row if request.param else None,
    ))


@pytest.mark.parametrize("memory", [False, True])
def test_stale_save_cannot_revive_or_replace_tombstone(database_url, account, memory):
    repo = InMemorySessionRepository() if memory else SqlSessionRepository(
        lambda: psycopg.connect(database_url)
    )
    service = SessionService(repo)
    stale = service.create_session(account, "local_password")
    original = dataclasses.replace(stale)
    when = datetime.now(UTC)
    repo.revoke(stale.session_id, "admin_manual_revoke", when)
    repo.save(stale)
    repo.revoke(stale.session_id, "user_logout", when + timedelta(seconds=1))
    persisted = repo.find_by_id(stale.session_id)
    assert persisted.revoked_at == when
    assert persisted.revoked_reason == "admin_manual_revoke"
    assert persisted.absolute_expires_at == original.absolute_expires_at
    assert persisted.account_id == original.account_id
    assert service.validate_session(stale.session_id) is None
    assert repo.find_active_by_account(account) == []


@pytest.mark.parametrize("field,value", [
    ("account_id", uuid4()), ("provider", "oidc"),
    ("created_at", datetime(2020, 1, 1, tzinfo=UTC)),
    ("absolute_expires_at", datetime(2100, 1, 1, tzinfo=UTC)),
    ("rotated_from", uuid4()),
])
def test_memory_save_preserves_identity(field, value):
    repo = InMemorySessionRepository()
    source = SessionService(repo).create_session(uuid4(), "local_password")
    with pytest.raises(ValueError, match="immutable"):
        repo.save(dataclasses.replace(source, **{field: value}))
    assert repo.find_by_id(source.session_id) == source


def test_memory_reads_do_not_alias_authoritative_records():
    repo = InMemorySessionRepository()
    source = SessionService(repo).create_session(uuid4(), "local_password")
    source.revoked_at = datetime.now(UTC)
    assert repo.find_by_id(source.session_id).revoked_at is None
    read = repo.find_by_id(source.session_id)
    read.account_id = uuid4()
    assert repo.find_by_id(source.session_id).account_id == source.account_id


@pytest.mark.parametrize("field", ["idle_expires_at", "absolute_expires_at"])
def test_memory_rotation_revalidates_deadlines(field):
    repo = InMemorySessionRepository()
    service = SessionService(repo)
    old = service.create_session(uuid4(), "local_password")
    # Expired at insertion, not a mutation of a detached returned snapshot.
    expired = dataclasses.replace(old, session_id=uuid4(), **{
        field: datetime.now(UTC) - timedelta(seconds=1),
    })
    repo.save(expired)
    with pytest.raises(SessionRotationRefused):
        service.rotate_session(expired)
    assert repo.find_by_id(expired.session_id).revoked_at is None


def test_unsupported_rotation_has_no_revoke_then_save_fallback():
    class Unsupported:
        def save(self, session):
            pytest.fail("no save on unsupported rotation")

        def revoke(self, *args):
            pytest.fail("no revoke on unsupported rotation")

    source = SessionService(InMemorySessionRepository()).create_session(uuid4(), "oidc")
    with pytest.raises(SessionStorageUnavailable, match="atomic rotation"):
        SessionService(Unsupported()).rotate_session(source)


@pytest.mark.parametrize("factory", [None, lambda: None])
@pytest.mark.parametrize("operation", ["create", "save", "revoke", "revoke-all", "touch", "rotate"])
def test_missing_storage_never_acknowledges_mutation(factory, operation):
    repo = SqlSessionRepository(factory)
    service = SessionService(repo)
    source = SessionService(InMemorySessionRepository()).create_session(uuid4(), "local_password")
    calls = {
        "create": lambda: service.create_session(source.account_id, "local_password"),
        "save": lambda: repo.save(source),
        "revoke": lambda: service.logout(source.session_id),
        "revoke-all": lambda: service.on_account_disabled(source.account_id),
        "touch": lambda: service.touch_session(source),
        "rotate": lambda: service.rotate_session(source),
    }
    with pytest.raises(SessionStorageUnavailable):
        calls[operation]()


def test_pg_rotation_is_durable_linked_and_absolute_bounded(repository, account):
    service = SessionService(repository)
    source = service.create_session(account, "local_password")
    replacement = service.rotate_session(source)
    assert replacement.session_id != source.session_id
    assert replacement.rotated_from == source.session_id
    assert replacement.absolute_expires_at == source.absolute_expires_at
    assert replacement.idle_expires_at <= replacement.absolute_expires_at
    assert repository.find_by_id(source.session_id).revoked_reason == "session_rotation"
    assert repository.find_by_id(replacement.session_id) == replacement
    assert service.validate_session(source.session_id) is None
    with pytest.raises(SessionRotationRefused):
        service.rotate_session(source)
    assert len(repository.find_active_by_account(account)) == 1


def test_pg_collision_rolls_back_source_revocation(repository, account):
    service = SessionService(repository)
    source = service.create_session(account, "oidc")
    collision = service.create_session(account, "oidc")
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.rotate(source, collision.session_id, reason="session_rotation",
                          now=datetime.now(UTC), idle_timeout=timedelta(minutes=30))
    assert repository.find_by_id(source.session_id).revoked_at is None
    assert repository.find_by_id(collision.session_id) == collision
    assert len(repository.find_active_by_account(account)) == 2


@pytest.mark.parametrize("field,value", [
    ("account_id", uuid4()), ("provider", "oidc"), ("rotated_from", uuid4()),
])
def test_pg_rotation_refuses_wrong_source_identity(repository, account, field, value):
    source = SessionService(repository).create_session(account, "local_password")
    with pytest.raises(SessionRotationRefused):
        SessionService(repository).rotate_session(dataclasses.replace(source, **{field: value}))
    assert repository.find_by_id(source.session_id).revoked_at is None


@pytest.mark.parametrize("deadline", ["idle_expires_at", "absolute_expires_at"])
def test_pg_rotation_refuses_persisted_expiry(repository, account, deadline):
    source = SessionService(repository).create_session(account, "local_password")
    # Change the authoritative row, retaining the stale caller snapshot.
    with psycopg.connect(os.environ["INTAKE_TEST_DATABASE_URL"]) as conn:
        conn.execute(f"UPDATE identity.sessions SET {deadline} = now() - interval '1 second' "
                     "WHERE session_id = %s", (source.session_id,))
    with pytest.raises(SessionRotationRefused):
        SessionService(repository).rotate_session(source)
    assert repository.find_by_id(source.session_id).revoked_at is None


def test_pg_save_rejects_immutable_identity(repository, account):
    source = SessionService(repository).create_session(account, "local_password")
    with pytest.raises(ValueError, match="immutable"):
        repository.save(dataclasses.replace(source, provider="oidc"))
    assert repository.find_by_id(source.session_id) == source


def test_pg_uncommitted_caller_transaction_cannot_claim_rotation(database_url, account):
    repo = SqlSessionRepository(lambda: psycopg.connect(database_url))
    source = SessionService(repo).create_session(account, "local_password")
    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT 1")
        with pytest.raises(SessionStorageUnavailable, match="idle connection"):
            SessionService(SqlSessionRepository(lambda: conn)).rotate_session(source)
    assert repo.find_by_id(source.session_id).revoked_at is None


@pytest.mark.parametrize("rotate_first", [True, False], ids=["rotation-lock-first", "revoke-lock-first"])
@pytest.mark.parametrize("autocommit", [False, True])
def test_pg_rotation_serializes_with_account_revocation(database_url, account, rotate_first, autocommit):
    """Actual independently connected transactions, both contested lock orders."""
    locked, release = Event(), Event()
    second_pid = Queue()
    pids = []

    class PausingCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            result = super().execute(query, params, **kwargs)
            if isinstance(query, str) and "FROM identity.accounts" in query and "FOR UPDATE" in query:
                locked.set()
                assert release.wait(5), "bounded test lock release"
            return result

    @contextmanager
    def first_connection():
        with psycopg.connect(database_url, autocommit=autocommit, cursor_factory=PausingCursor) as conn:
            pids.append(conn.info.backend_pid)
            yield conn

    @contextmanager
    def second_connection():
        with psycopg.connect(database_url, autocommit=autocommit) as conn:
            pids.append(conn.info.backend_pid)
            second_pid.put(conn.info.backend_pid)
            yield conn

    normal = SqlSessionRepository(lambda: psycopg.connect(database_url))
    source = SessionService(normal).create_session(account, "local_password")
    other = SessionService(normal).create_session(account, "local_password")
    first, second = SqlSessionRepository(first_connection), SqlSessionRepository(second_connection)
    def rotate(repo):
        return SessionService(repo).rotate_session(source)

    def revoke(repo):
        return repo.revoke_all_for_account(account, "admin_manual_revoke")

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(rotate if rotate_first else revoke, first)
        try:
            assert locked.wait(5)
            two = executor.submit(revoke if rotate_first else rotate, second)
            waiting_pid = second_pid.get(timeout=5)
            deadline = time.monotonic() + 5
            with psycopg.connect(database_url, autocommit=True) as observer:
                while True:
                    blockers = observer.execute("SELECT pg_blocking_pids(%s)", (waiting_pid,)).fetchone()[0]
                    if pids[0] in blockers:
                        break
                    assert time.monotonic() < deadline, "second transaction must actually contend"
                    time.sleep(.01)
        finally:
            release.set()
        if rotate_first:
            replacement = one.result(timeout=5)
            assert two.result(timeout=5) == 2  # replacement plus independent existing session
            assert normal.find_by_id(replacement.session_id).revoked_reason == "admin_manual_revoke"
        else:
            assert one.result(timeout=5) == 2
            with pytest.raises(SessionRotationRefused):
                two.result(timeout=5)
    assert len(set(pids)) == 2
    assert normal.find_by_id(source.session_id).revoked_at is not None
    assert normal.find_by_id(other.session_id).revoked_at is not None
    assert normal.find_active_by_account(account) == []
    # A genuine independent login AFTER revocation is not prohibited by a
    # guessed account-wide permanent tombstone or compensating revocation.
    later = SessionService(normal).create_session(account, "local_password")
    assert SessionService(normal).validate_session(later.session_id) is not None


def test_memory_concurrent_rotations_have_one_winner():
    repo = InMemorySessionRepository()
    source = SessionService(repo).create_session(uuid4(), "local_password")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(SessionService(repo).rotate_session, source) for _ in range(2)]
    winners, refusals = [], []
    for future in futures:
        try:
            winners.append(future.result())
        except SessionRotationRefused as exc:
            refusals.append(exc)
    assert len(winners) == len(refusals) == 1
    assert repo.find_active_by_account(source.account_id) == winners
