"""identity.sessions 持久化與連線生命週期的回歸測試。

Task: ODP-WEB-LOCAL-AUTH-API-TRUST-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5.1, §5.4

涵蓋 review 指出的第三項缺陷：
1. PostgreSQL 模式必須接上 identity.sessions 的持久 repository，否則撤銷只在
   單一 process 內生效。
2. 連線必須歸還 pool；只 getconn 不 putconn 會耗盡連線池。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from shared.identity import (
    InMemorySessionRepository,
    Session,
    SessionService,
    SqlIdentityStore,
    SqlSessionRepository,
)


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection
        self.rowcount = 1

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._connection.executed.append((" ".join(sql.split()), params))

    def fetchone(self) -> Any:
        return self._connection.next_row

    def fetchall(self) -> list[Any]:
        return self._connection.next_rows


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.next_row: Any = None
        self.next_rows: list[Any] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    # The engine's own transaction/execute surface, used when a bundle is
    # constructed over this fake pool.
    @contextmanager
    def transaction(self) -> Any:
        yield self

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        cursor = _FakeCursor(self)
        cursor.execute(sql, params)
        return cursor


class _FakePool:
    """A pool that fails the test if a borrowed connection is never returned."""

    def __init__(self) -> None:
        self.connection_obj = _FakeConnection()
        self.checked_out = 0
        self.peak_checked_out = 0
        self.borrows = 0

    @contextmanager
    def connection(self) -> Any:
        self.borrows += 1
        self.checked_out += 1
        self.peak_checked_out = max(self.peak_checked_out, self.checked_out)
        try:
            yield self.connection_obj
        finally:
            self.checked_out -= 1


@pytest.fixture
def pool() -> _FakePool:
    return _FakePool()


def _session(account_id: Any = None, session_id: Any = None) -> Session:
    now = datetime.now(UTC)
    return Session(
        session_id=session_id or uuid4(),
        account_id=account_id or uuid4(),
        provider="local_password",
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
    )


# --- Durable session persistence (Contract §5.1) ---------------------------


def test_sql_session_repository_writes_revocation_to_identity_sessions(pool: _FakePool):
    """撤銷必須寫進 identity.sessions，而不是只留在 process 記憶體。"""
    repo = SqlSessionRepository(connection_factory=pool.connection)
    session = _session()

    repo.revoke(session.session_id, "admin_manual_revoke")

    statements = [sql for sql, _ in pool.connection_obj.executed]
    assert any("UPDATE identity.sessions" in sql for sql in statements)
    assert any("revoked_at" in sql and "revoked_reason" in sql for sql in statements)
    assert pool.connection_obj.commits == 1


def test_sql_session_repository_round_trips_a_session(pool: _FakePool):
    session = _session()
    pool.connection_obj.next_row = (
        str(session.session_id),
        str(session.account_id),
        "local_password",
        session.created_at,
        session.last_seen_at,
        session.idle_expires_at,
        session.absolute_expires_at,
        None,
        None,
        None,
    )
    repo = SqlSessionRepository(connection_factory=pool.connection)

    found = repo.find_by_id(session.session_id)

    assert found is not None
    assert found.session_id == session.session_id
    assert found.account_id == session.account_id
    assert found.provider == "local_password"
    assert found.is_active is True


def test_sql_session_repository_revoke_all_excludes_current_session(pool: _FakePool):
    repo = SqlSessionRepository(connection_factory=pool.connection)
    account_id = uuid4()
    keep = uuid4()

    repo.revoke_all_for_account(account_id, "password_change", except_session_id=keep)

    sql, params = pool.connection_obj.executed[-1]
    assert "session_id <> %s" in sql
    assert params[-1] == str(keep)


def test_session_service_over_sql_repository_denies_revoked_session(pool: _FakePool):
    """撤銷後 validate_session 必須回 None（§5.4 立即生效）。"""
    session = _session()
    revoked_at = datetime.now(UTC)
    pool.connection_obj.next_row = (
        str(session.session_id),
        str(session.account_id),
        "local_password",
        session.created_at,
        session.last_seen_at,
        session.idle_expires_at,
        session.absolute_expires_at,
        revoked_at,
        "admin_manual_revoke",
        None,
    )
    service = SessionService(repository=SqlSessionRepository(connection_factory=pool.connection))

    assert service.validate_session(session.session_id) is None


# --- Connection lifecycle (pool exhaustion) --------------------------------


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda store, pool: store.find_account_by_id(uuid4()),
            id="identity_store.find_account_by_id",
        ),
        pytest.param(
            lambda store, pool: store.get_account_roles(uuid4()),
            id="identity_store.get_account_roles",
        ),
        pytest.param(
            lambda store, pool: store.get_account_scope(uuid4()),
            id="identity_store.get_account_scope",
        ),
    ],
)
def test_sql_identity_store_returns_pooled_connections(pool: _FakePool, call):
    """每次查詢借一條連線並歸還；否則 pool 會被耗盡。"""
    store = SqlIdentityStore(connection_factory=pool.connection)

    for _ in range(5):
        call(store, pool)

    assert pool.borrows == 5
    assert pool.checked_out == 0, "borrowed connection was never returned to the pool"
    assert pool.peak_checked_out == 1, "a single query must not hold two connections"


def test_sql_session_repository_returns_pooled_connections(pool: _FakePool):
    repo = SqlSessionRepository(connection_factory=pool.connection)

    for _ in range(5):
        repo.find_by_id(uuid4())
        repo.revoke(uuid4(), "user_logout")

    assert pool.checked_out == 0
    assert pool.peak_checked_out == 1


def test_open_connection_leaves_caller_owned_connections_alone():
    """裸連線（非 context manager）由呼叫端擁有，這裡不得關閉。"""
    from shared.identity.sql_support import open_connection

    connection = _FakeConnection()
    with open_connection(lambda: connection) as acquired:
        assert acquired is connection

    connection_2 = _FakeConnection()
    with open_connection(connection_2) as acquired:
        assert acquired is connection_2


def test_open_connection_yields_none_when_unavailable():
    from shared.identity.sql_support import open_connection

    with open_connection(lambda: None) as acquired:
        assert acquired is None
    with open_connection(None) as acquired:
        assert acquired is None


def test_postgres_engine_pooled_connection_returns_to_pool(pool: _FakePool):
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    engine = PostgresEngine(
        "postgresql://user:pass@localhost:5432/odp",
        pool=pool,
        bootstrap=False,
        validate_schema=False,
    )

    with engine.pooled_connection() as connection:
        assert connection is pool.connection_obj
        assert pool.checked_out == 1

    assert pool.checked_out == 0


# --- PostgreSQL bundle wiring ----------------------------------------------


def test_postgres_bundle_wires_durable_session_repository(monkeypatch: pytest.MonkeyPatch):
    """PostgreSQL 模式必須用 SqlSessionRepository，不得用 InMemorySessionRepository。

    用 in-memory repository 會讓撤銷只在單一 instance 生效，其他 instance 仍把
    已撤銷的 sid 當成有效（Contract §5.1、§5.4）。
    """
    from shared.infrastructure.persistence import factory, postgresql

    fake_pool = _FakePool()
    real_engine_cls = postgresql.PostgresEngine

    def _engine_factory(database_url: str, **kwargs: Any) -> Any:
        # Same engine class, but backed by a fake pool so no database is needed.
        return real_engine_cls(
            database_url,
            pool=fake_pool,
            bootstrap=False,
            validate_schema=False,
        )

    monkeypatch.setattr(postgresql, "PostgresEngine", _engine_factory)
    monkeypatch.setattr(
        "shared.infrastructure.persistence.assisted_listing_intake.validate_required_tables",
        lambda engine: None,
    )

    bundle = factory._postgres_bundle("postgresql://user:pass@localhost:5432/odp")

    repository = bundle.session_service._repo
    assert isinstance(repository, SqlSessionRepository)
    assert not isinstance(repository, InMemorySessionRepository)
    assert isinstance(bundle.identity_store, SqlIdentityStore)


# --- Production dict-row regression tests (psycopg dict_row factory) -------
#
# PostgresEngine._build_pool sets row_factory=dict_row.  These tests verify
# that SqlIdentityStore and SqlSessionRepository correctly handle dict rows
# returned by the production psycopg cursor, not just the tuple fakes used
# above.


def _dict_row_session(session: Session, *, revoked: bool = False) -> dict:
    """Build a production-shaped dict row for identity.sessions."""
    return {
        "session_id": str(session.session_id),
        "account_id": str(session.account_id),
        "provider": session.provider,
        "created_at": session.created_at,
        "last_seen_at": session.last_seen_at,
        "idle_expires_at": session.idle_expires_at,
        "absolute_expires_at": session.absolute_expires_at,
        "revoked_at": datetime.now(UTC) if revoked else None,
        "revoked_reason": "admin_manual_revoke" if revoked else None,
        "rotated_from": None,
    }


def test_sql_session_repository_round_trips_dict_row(pool: _FakePool):
    """Production psycopg dict_row must round-trip through _row_to_session."""
    session = _session()
    pool.connection_obj.next_row = _dict_row_session(session)
    repo = SqlSessionRepository(connection_factory=pool.connection)

    found = repo.find_by_id(session.session_id)

    assert found is not None
    assert found.session_id == session.session_id
    assert found.account_id == session.account_id
    assert found.provider == "local_password"
    assert found.is_active is True


def test_sql_session_repository_revoked_dict_row(pool: _FakePool):
    """Revoked dict row must be recognized as revoked (§5.4)."""
    session = _session()
    pool.connection_obj.next_row = _dict_row_session(session, revoked=True)
    service = SessionService(repository=SqlSessionRepository(connection_factory=pool.connection))

    assert service.validate_session(session.session_id) is None


def test_sql_session_repository_active_by_account_dict_rows(pool: _FakePool):
    """find_active_by_account must handle a list of dict rows."""
    account_id = uuid4()
    s1 = _session(account_id=account_id)
    s2 = _session(account_id=account_id)
    pool.connection_obj.next_rows = [
        _dict_row_session(s1),
        _dict_row_session(s2),
    ]
    repo = SqlSessionRepository(connection_factory=pool.connection)

    active = repo.find_active_by_account(account_id)

    assert len(active) == 2
    assert {s.session_id for s in active} == {s1.session_id, s2.session_id}


def _dict_row_account(
    account_id: Any = None,
    tenant_id: Any = None,
    username: str = "testuser",
    email: str = "test@example.com",
) -> dict:
    """Build a production-shaped dict row for identity.accounts."""
    now = datetime.now(UTC)
    return {
        "account_id": str(account_id or uuid4()),
        "tenant_id": str(tenant_id or uuid4()),
        "username": username,
        "email": email,
        "display_name": "Test User",
        "status": "active",
        "created_at": now,
        "created_by": "system",
        "updated_at": now,
        "disabled_at": None,
        "disabled_reason": None,
    }


def test_sql_identity_store_find_account_by_id_dict_row(pool: _FakePool):
    """find_account_by_id must handle production dict rows."""
    from uuid import UUID

    aid = uuid4()
    tid = uuid4()
    pool.connection_obj.next_row = _dict_row_account(account_id=aid, tenant_id=tid)
    store = SqlIdentityStore(connection_factory=pool.connection)

    account = store.find_account_by_id(aid)

    assert account is not None
    assert account.account_id == aid
    assert account.tenant_id == tid
    assert account.username == "testuser"
    assert account.email == "test@example.com"
    assert account.status == "active"


def test_sql_identity_store_find_account_by_username_dict_row(pool: _FakePool):
    """find_account_by_username must handle production dict rows."""
    tid = uuid4()
    pool.connection_obj.next_row = _dict_row_account(tenant_id=tid, username="alice")
    store = SqlIdentityStore(connection_factory=pool.connection)

    account = store.find_account_by_username(tid, "alice")

    assert account is not None
    assert account.username == "alice"


def test_sql_identity_store_find_account_by_email_dict_row(pool: _FakePool):
    """find_account_by_email must handle production dict rows."""
    tid = uuid4()
    pool.connection_obj.next_row = _dict_row_account(tenant_id=tid, email="alice@corp.com")
    store = SqlIdentityStore(connection_factory=pool.connection)

    account = store.find_account_by_email(tid, "alice@corp.com")

    assert account is not None
    assert account.email == "alice@corp.com"


def test_sql_identity_store_find_federated_identity_dict_row(pool: _FakePool):
    """find_account_by_federated_identity must handle production dict rows."""
    aid = uuid4()
    pool.connection_obj.next_row = _dict_row_account(account_id=aid)
    store = SqlIdentityStore(connection_factory=pool.connection)

    account = store.find_account_by_federated_identity("https://accounts.google.com", "sub-123")

    assert account is not None
    assert account.account_id == aid


def test_sql_identity_store_get_account_roles_dict_rows(pool: _FakePool):
    """get_account_roles must handle production dict rows."""
    from shared.auth import Role

    pool.connection_obj.next_rows = [
        {"role": "operations_manager"},
        {"role": "auditor"},
    ]
    store = SqlIdentityStore(connection_factory=pool.connection)

    roles = store.get_account_roles(uuid4())

    assert Role.OPERATIONS_MANAGER in roles
    assert Role.AUDITOR in roles


def test_sql_identity_store_get_account_scope_dict_row(pool: _FakePool):
    """get_account_scope must handle production dict rows for the scope join."""
    from shared.auth import DataClassification

    aid = uuid4()
    tid = uuid4()
    pool.connection_obj.next_row = {
        "brand_ids": ["brand-1", "brand-2"],
        "region_ids": ["region-1"],
        "store_ids": [],
        "assigned_area_ids": [],
        "heat_zone_ids": [],
        "modules": ["forecasting"],
        "clearance": "confidential",
        "tenant_id": str(tid),
    }
    store = SqlIdentityStore(connection_factory=pool.connection)

    scope = store.get_account_scope(aid)

    assert scope.tenant_id == str(tid)
    assert "brand-1" in scope.brand_ids
    assert "brand-2" in scope.brand_ids
    assert "region-1" in scope.region_ids
    assert "forecasting" in scope.modules
    assert scope.clearance == DataClassification.CONFIDENTIAL


def test_sql_identity_store_get_account_scope_no_scope_dict_row(pool: _FakePool):
    """get_account_scope fallback (no scope row) must handle dict rows."""
    tid = uuid4()
    # First query returns no scope row; second returns the account's tenant_id
    original_fetchone = pool.connection_obj.__class__.cursor

    call_count = {"n": 0}
    original_next_row = pool.connection_obj.next_row

    class _CountingCursor(_FakeCursor):
        def fetchone(self) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # no scope row
            return {"tenant_id": str(tid)}  # fallback account lookup

    class _CountingConnection(_FakeConnection):
        def cursor(self) -> _CountingCursor:
            return _CountingCursor(self)

    counting_conn = _CountingConnection()
    pool.connection_obj = counting_conn

    store = SqlIdentityStore(connection_factory=pool.connection)

    scope = store.get_account_scope(uuid4())

    assert scope.tenant_id == str(tid)

