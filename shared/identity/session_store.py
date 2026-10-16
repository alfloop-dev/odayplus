"""``identity.sessions`` 的持久 session repository。

Task: ODP-WEB-LOCAL-AUTH-API-TRUST-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5

契約 §5.1 規定「權威 session 狀態在 ``identity.sessions``」。在 PostgreSQL
執行環境用 ``InMemorySessionRepository`` 會讓撤銷只在單一 process 內生效：
另一個 Cloud Run instance 仍會把已撤銷的 sid 視為有效，違反 §5.4 的撤銷傳播
上界。本模組提供對應的 SQL 實作。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from .session_service import (
    Session,
    SessionRotationRefused,
    SessionStorageUnavailable,
    _rotation_replacement,
)
from .sql_support import open_connection


def _row_to_session(row: Any) -> Session:
    """Convert a database row to a Session.

    Supports both dict rows (production psycopg ``dict_row`` factory) and
    tuple/sequence rows (test fakes).
    """
    if isinstance(row, dict):
        rotated = row.get("rotated_from")
        return Session(
            session_id=UUID(str(row["session_id"])),
            account_id=UUID(str(row["account_id"])),
            provider=row["provider"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            idle_expires_at=row["idle_expires_at"],
            absolute_expires_at=row["absolute_expires_at"],
            revoked_at=row.get("revoked_at"),
            revoked_reason=row.get("revoked_reason"),
            rotated_from=UUID(str(rotated)) if rotated else None,
        )
    # Fallback: tuple / sequence row (legacy or test fakes)
    return Session(
        session_id=UUID(str(row[0])),
        account_id=UUID(str(row[1])),
        provider=row[2],
        created_at=row[3],
        last_seen_at=row[4],
        idle_expires_at=row[5],
        absolute_expires_at=row[6],
        revoked_at=row[7],
        revoked_reason=row[8],
        rotated_from=UUID(str(row[9])) if row[9] else None,
    )


class SqlSessionRepository:
    """PostgreSQL ``identity.sessions`` 持久化實作。

    ``connection_factory`` 語意見 :mod:`shared.identity.sql_support`：交出
    context manager 時連線由 pool 擁有並在使用後歸還。
    """

    def __init__(self, connection_factory: Any = None) -> None:
        self._conn_factory = connection_factory

    def save(self, session: Session) -> None:
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                raise SessionStorageUnavailable("session storage unavailable")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO identity.sessions (
                        session_id, account_id, provider, created_at, last_seen_at,
                        idle_expires_at, absolute_expires_at, revoked_at,
                        revoked_reason, rotated_from
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        last_seen_at = GREATEST(identity.sessions.last_seen_at, EXCLUDED.last_seen_at),
                        idle_expires_at = LEAST(identity.sessions.absolute_expires_at,
                            GREATEST(identity.sessions.idle_expires_at, EXCLUDED.idle_expires_at)),
                        revoked_at = COALESCE(identity.sessions.revoked_at, EXCLUDED.revoked_at),
                        revoked_reason = CASE WHEN identity.sessions.revoked_at IS NOT NULL
                            THEN identity.sessions.revoked_reason ELSE EXCLUDED.revoked_reason END
                    WHERE identity.sessions.account_id = EXCLUDED.account_id
                        AND identity.sessions.provider = EXCLUDED.provider
                        AND identity.sessions.created_at = EXCLUDED.created_at
                        AND identity.sessions.absolute_expires_at = EXCLUDED.absolute_expires_at
                        AND identity.sessions.rotated_from IS NOT DISTINCT FROM EXCLUDED.rotated_from
                    """,
                    (
                        str(session.session_id),
                        str(session.account_id),
                        session.provider,
                        session.created_at,
                        session.last_seen_at,
                        session.idle_expires_at,
                        session.absolute_expires_at,
                        session.revoked_at,
                        session.revoked_reason,
                        str(session.rotated_from) if session.rotated_from else None,
                    ),
                )
                if cur.rowcount != 1:
                    raise ValueError("session identity is immutable")
                conn.commit()

    def rotate(
        self, expected: Session, replacement_id: UUID, *, reason: str,
        now: datetime, idle_timeout: timedelta,
    ) -> Session:
        """One owned transaction, account lock before source lock.

        Account-wide revocation takes the SAME account-row lock. A rotation
        that wins first exposes its replacement to that revocation; a rotation
        that loses re-reads a revoked source and refuses. Independent future
        logins are not prohibited. No schema or account-wide compensation.
        """
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                raise SessionStorageUnavailable("session storage unavailable")
            # Do not report durable issuance from a caller's uncommitted outer
            # transaction/savepoint. The repository owns this boundary.
            if conn.info.transaction_status != 0:
                raise SessionStorageUnavailable("rotation requires an idle connection")
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT status FROM identity.accounts WHERE account_id = %s FOR UPDATE",
                        (str(expected.account_id),),
                    )
                    account = cur.fetchone()
                    status = account.get("status") if isinstance(account, dict) else (
                        account[0] if account else None
                    )
                    if status != "active":
                        raise SessionRotationRefused("rotation account unavailable or inactive")
                    cur.execute(
                        """
                        SELECT session_id, account_id, provider, created_at, last_seen_at,
                               idle_expires_at, absolute_expires_at, revoked_at,
                               revoked_reason, rotated_from
                        FROM identity.sessions WHERE session_id = %s FOR UPDATE
                        """,
                        (str(expected.session_id),),
                    )
                    row = cur.fetchone()
                    replacement = _rotation_replacement(
                        _row_to_session(row) if row else None, expected, replacement_id,
                        reason=reason, now=now, idle_timeout=idle_timeout,
                    )
                    cur.execute(
                        """UPDATE identity.sessions SET revoked_at = %s, revoked_reason = %s
                           WHERE session_id = %s AND revoked_at IS NULL""",
                        (replacement.created_at, reason, str(expected.session_id)),
                    )
                    if cur.rowcount != 1:
                        raise SessionRotationRefused("rotation source changed")
                    # No upsert: replacement collision rolls back source revocation.
                    cur.execute(
                        """
                        INSERT INTO identity.sessions (
                            session_id, account_id, provider, created_at, last_seen_at,
                            idle_expires_at, absolute_expires_at, rotated_from
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (str(replacement.session_id), str(replacement.account_id),
                         replacement.provider, replacement.created_at, replacement.last_seen_at,
                         replacement.idle_expires_at, replacement.absolute_expires_at,
                         str(replacement.rotated_from)),
                    )
            return replacement

    def find_by_id(self, session_id: UUID) -> Session | None:
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, account_id, provider, created_at, last_seen_at,
                           idle_expires_at, absolute_expires_at, revoked_at,
                           revoked_reason, rotated_from
                    FROM identity.sessions
                    WHERE session_id = %s
                    """,
                    (str(session_id),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return _row_to_session(row)

    def find_active_by_account(self, account_id: UUID) -> list[Session]:
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return []
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, account_id, provider, created_at, last_seen_at,
                           idle_expires_at, absolute_expires_at, revoked_at,
                           revoked_reason, rotated_from
                    FROM identity.sessions
                    WHERE account_id = %s AND revoked_at IS NULL
                    """,
                    (str(account_id),),
                )
                rows = cur.fetchall() or []
        # is_active also applies the idle/absolute deadlines, which the
        # revoked_at filter alone does not cover.
        return [s for s in (_row_to_session(row) for row in rows) if s.is_active]

    def revoke(self, session_id: UUID, reason: str, revoked_at: datetime | None = None) -> None:
        ts = revoked_at or datetime.now(UTC)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                raise SessionStorageUnavailable("session storage unavailable")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE identity.sessions
                    SET revoked_at = %s, revoked_reason = %s
                    WHERE session_id = %s AND revoked_at IS NULL
                    """,
                    (ts, reason, str(session_id)),
                )
                conn.commit()

    def revoke_all_for_account(
        self,
        account_id: UUID,
        reason: str,
        *,
        except_session_id: UUID | None = None,
        revoked_at: datetime | None = None,
    ) -> int:
        ts = revoked_at or datetime.now(UTC)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                raise SessionStorageUnavailable("session storage unavailable")
            # Explicit transaction also covers autocommit connections; a row
            # lock acquired in a standalone statement would not coordinate.
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT account_id FROM identity.accounts WHERE account_id = %s FOR UPDATE",
                        (str(account_id),),
                    )
                    if except_session_id is None:
                        cur.execute(
                            """
                            UPDATE identity.sessions
                            SET revoked_at = %s, revoked_reason = %s
                            WHERE account_id = %s AND revoked_at IS NULL
                            """,
                            (ts, reason, str(account_id)),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE identity.sessions
                            SET revoked_at = %s, revoked_reason = %s
                            WHERE account_id = %s AND revoked_at IS NULL AND session_id <> %s
                            """,
                            (ts, reason, str(account_id), str(except_session_id)),
                        )
                    revoked = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            return revoked

    def update_last_seen(
        self, session_id: UUID, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None:
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                raise SessionStorageUnavailable("session storage unavailable")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE identity.sessions
                    SET last_seen_at = GREATEST(last_seen_at, %s),
                        idle_expires_at = LEAST(absolute_expires_at, GREATEST(idle_expires_at, %s))
                    WHERE session_id = %s AND revoked_at IS NULL
                    """,
                    (last_seen_at, idle_expires_at, str(session_id)),
                )
                conn.commit()
