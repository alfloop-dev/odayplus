"""``identity.sessions`` 的持久 session repository。

Task: ODP-WEB-LOCAL-AUTH-API-TRUST-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5

契約 §5.1 規定「權威 session 狀態在 ``identity.sessions``」。在 PostgreSQL
執行環境用 ``InMemorySessionRepository`` 會讓撤銷只在單一 process 內生效：
另一個 Cloud Run instance 仍會把已撤銷的 sid 視為有效，違反 §5.4 的撤銷傳播
上界。本模組提供對應的 SQL 實作。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .session_service import Session
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
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO identity.sessions (
                        session_id, account_id, provider, created_at, last_seen_at,
                        idle_expires_at, absolute_expires_at, revoked_at,
                        revoked_reason, rotated_from
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        last_seen_at = EXCLUDED.last_seen_at,
                        idle_expires_at = EXCLUDED.idle_expires_at,
                        absolute_expires_at = EXCLUDED.absolute_expires_at,
                        revoked_at = EXCLUDED.revoked_at,
                        revoked_reason = EXCLUDED.revoked_reason
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
                conn.commit()

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
                return
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
                return 0
            with conn.cursor() as cur:
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
                conn.commit()
                return revoked

    def update_last_seen(
        self, session_id: UUID, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None:
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE identity.sessions
                    SET last_seen_at = %s, idle_expires_at = %s
                    WHERE session_id = %s AND revoked_at IS NULL
                    """,
                    (last_seen_at, idle_expires_at, str(session_id)),
                )
                conn.commit()
