"""Identity store repository and data models for the PostgreSQL identity schema.

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2, §3, §4
- identity.accounts: account_id, tenant_id, username, email, display_name, status, etc.
- identity.account_roles: account_id, role, granted_at, granted_by
- identity.account_scopes: account_id, brand_ids, region_ids, store_ids, assigned_area_ids, heat_zone_ids, modules, clearance
- identity.federated_identities: account_id, issuer, subject, linked_at, linked_by
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from shared.auth import DataClassification, Role, Scope

from .sql_support import open_connection


@dataclasses.dataclass(frozen=True)
class Account:
    """帳號主表記錄（對應 identity.accounts 表）。"""

    account_id: UUID
    tenant_id: UUID
    username: str
    email: str
    display_name: str = ""
    status: str = "active"  # "invited" | "active" | "disabled" | "locked"
    created_at: datetime | None = None
    created_by: str = "system"
    updated_at: datetime | None = None
    disabled_at: datetime | None = None
    disabled_reason: str | None = None

    @property
    def is_active(self) -> bool:
        """帳號是否處於啟用狀態。"""
        return self.status == "active"


@dataclasses.dataclass(frozen=True)
class FederatedIdentity:
    """OIDC 聯合身份記錄（對應 identity.federated_identities 表）。"""

    account_id: UUID
    issuer: str
    subject: str
    linked_at: datetime | None = None
    linked_by: str = "system"


def _parse_uuid(val: UUID | str) -> UUID:
    if isinstance(val, UUID):
        return val
    return UUID(str(val))


class IdentityStore(Protocol):
    """權威身份來源（identity schema）查詢與管理介面。"""

    def find_account_by_id(self, account_id: UUID | str) -> Account | None:
        """以 account_id 查找帳號。"""
        ...

    def find_account_by_username(self, tenant_id: UUID | str, username: str) -> Account | None:
        """以 tenant_id 與 username 查找帳號（case-insensitive）。"""
        ...

    def find_account_by_email(self, tenant_id: UUID | str, email: str) -> Account | None:
        """以 tenant_id 與 email 查找帳號（case-insensitive）。"""
        ...

    def find_account_by_federated_identity(self, issuer: str, subject: str) -> Account | None:
        """以 OIDC (issuer, subject) 查找對應帳號。"""
        ...

    def get_account_roles(self, account_id: UUID | str) -> frozenset[Role]:
        """取得帳號所擁有的有效平台角色。"""
        ...

    def get_account_scope(self, account_id: UUID | str) -> Scope:
        """取得帳號的資料範圍 (Scope)。"""
        ...

    def save_account(self, account: Account) -> None:
        """儲存或更新帳號記錄。"""
        ...

    def set_account_roles(self, account_id: UUID | str, roles: Iterable[Role | str]) -> None:
        """設定帳號角色。"""
        ...

    def set_account_scope(self, account_id: UUID | str, scope: Scope) -> None:
        """設定帳號資料範圍。"""
        ...

    def link_federated_identity(
        self,
        account_id: UUID | str,
        issuer: str,
        subject: str,
        linked_by: str = "system",
    ) -> None:
        """建立 OIDC (issuer, subject) 與 account_id 的聯合身份關聯。"""
        ...


class InMemoryIdentityStore:
    """測試與本機開發用的記憶體身份庫實作。"""

    def __init__(self) -> None:
        self._accounts: dict[UUID, Account] = {}
        self._roles: dict[UUID, set[Role]] = {}
        self._scopes: dict[UUID, Scope] = {}
        # (issuer, subject) -> account_id
        self._federated_links: dict[tuple[str, str], UUID] = {}

    def save_account(self, account: Account) -> None:
        self._accounts[account.account_id] = account

    def find_account_by_id(self, account_id: UUID | str) -> Account | None:
        aid = _parse_uuid(account_id)
        return self._accounts.get(aid)

    def find_account_by_username(self, tenant_id: UUID | str, username: str) -> Account | None:
        tid = _parse_uuid(tenant_id)
        u_lower = username.strip().lower()
        for acc in self._accounts.values():
            if acc.tenant_id == tid and acc.username.lower() == u_lower:
                return acc
        return None

    def find_account_by_email(self, tenant_id: UUID | str, email: str) -> Account | None:
        tid = _parse_uuid(tenant_id)
        e_lower = email.strip().lower()
        for acc in self._accounts.values():
            if acc.tenant_id == tid and acc.email.lower() == e_lower:
                return acc
        return None

    def find_account_by_federated_identity(self, issuer: str, subject: str) -> Account | None:
        aid = self._federated_links.get((issuer, subject))
        if aid is None:
            return None
        return self.find_account_by_id(aid)

    def link_federated_identity(
        self,
        account_id: UUID | str,
        issuer: str,
        subject: str,
        linked_by: str = "system",
    ) -> None:
        aid = _parse_uuid(account_id)
        self._federated_links[(issuer, subject)] = aid

    def set_account_roles(self, account_id: UUID | str, roles: Iterable[Role | str]) -> None:
        aid = _parse_uuid(account_id)
        parsed: set[Role] = set()
        for r in roles:
            if isinstance(r, Role):
                parsed.add(r)
            else:
                try:
                    parsed.add(Role(str(r)))
                except ValueError:
                    pass
        self._roles[aid] = parsed

    def get_account_roles(self, account_id: UUID | str) -> frozenset[Role]:
        aid = _parse_uuid(account_id)
        return frozenset(self._roles.get(aid, set()))

    def set_account_scope(self, account_id: UUID | str, scope: Scope) -> None:
        aid = _parse_uuid(account_id)
        self._scopes[aid] = scope

    def get_account_scope(self, account_id: UUID | str) -> Scope:
        aid = _parse_uuid(account_id)
        if aid in self._scopes:
            return self._scopes[aid]
        acc = self.find_account_by_id(aid)
        tenant_id = str(acc.tenant_id) if acc else None
        return Scope(tenant_id=tenant_id, clearance=DataClassification.CONFIDENTIAL)


class SqlIdentityStore:
    """PostgreSQL identity schema 查詢實作。

    ``connection_factory`` 語意見 :mod:`shared.identity.sql_support`：交出
    context manager 時（例如 ``PostgresEngine.pooled_connection``）連線由
    pool 擁有，每次查詢用完即歸還；交出裸連線時由呼叫端擁有。
    """

    def __init__(self, connection_factory: Any = None) -> None:
        self._conn_factory = connection_factory

    def find_account_by_id(self, account_id: UUID | str) -> Account | None:
        aid = _parse_uuid(account_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT account_id, tenant_id, username, email, display_name, status,
                           created_at, created_by, updated_at, disabled_at, disabled_reason
                    FROM identity.accounts
                    WHERE account_id = %s
                    """,
                    (str(aid),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Account(
                    account_id=UUID(str(row[0])),
                    tenant_id=UUID(str(row[1])),
                    username=row[2],
                    email=row[3],
                    display_name=row[4],
                    status=row[5],
                    created_at=row[6],
                    created_by=row[7],
                    updated_at=row[8],
                    disabled_at=row[9],
                    disabled_reason=row[10],
                )

    def find_account_by_username(self, tenant_id: UUID | str, username: str) -> Account | None:
        tid = _parse_uuid(tenant_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT account_id, tenant_id, username, email, display_name, status,
                           created_at, created_by, updated_at, disabled_at, disabled_reason
                    FROM identity.accounts
                    WHERE tenant_id = %s AND lower(username) = lower(%s)
                    """,
                    (str(tid), username.strip()),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Account(
                    account_id=UUID(str(row[0])),
                    tenant_id=UUID(str(row[1])),
                    username=row[2],
                    email=row[3],
                    display_name=row[4],
                    status=row[5],
                    created_at=row[6],
                    created_by=row[7],
                    updated_at=row[8],
                    disabled_at=row[9],
                    disabled_reason=row[10],
                )

    def find_account_by_email(self, tenant_id: UUID | str, email: str) -> Account | None:
        tid = _parse_uuid(tenant_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT account_id, tenant_id, username, email, display_name, status,
                           created_at, created_by, updated_at, disabled_at, disabled_reason
                    FROM identity.accounts
                    WHERE tenant_id = %s AND lower(email) = lower(%s)
                    """,
                    (str(tid), email.strip()),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Account(
                    account_id=UUID(str(row[0])),
                    tenant_id=UUID(str(row[1])),
                    username=row[2],
                    email=row[3],
                    display_name=row[4],
                    status=row[5],
                    created_at=row[6],
                    created_by=row[7],
                    updated_at=row[8],
                    disabled_at=row[9],
                    disabled_reason=row[10],
                )

    def find_account_by_federated_identity(self, issuer: str, subject: str) -> Account | None:
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.account_id, a.tenant_id, a.username, a.email, a.display_name, a.status,
                           a.created_at, a.created_by, a.updated_at, a.disabled_at, a.disabled_reason
                    FROM identity.federated_identities f
                    JOIN identity.accounts a ON f.account_id = a.account_id
                    WHERE f.issuer = %s AND f.subject = %s
                    """,
                    (issuer, subject),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Account(
                    account_id=UUID(str(row[0])),
                    tenant_id=UUID(str(row[1])),
                    username=row[2],
                    email=row[3],
                    display_name=row[4],
                    status=row[5],
                    created_at=row[6],
                    created_by=row[7],
                    updated_at=row[8],
                    disabled_at=row[9],
                    disabled_reason=row[10],
                )

    def get_account_roles(self, account_id: UUID | str) -> frozenset[Role]:
        aid = _parse_uuid(account_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return frozenset()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT role FROM identity.account_roles
                    WHERE account_id = %s
                    """,
                    (str(aid),),
                )
                rows = cur.fetchall()
                roles: set[Role] = set()
                for row in rows:
                    try:
                        roles.add(Role(row[0]))
                    except ValueError:
                        pass
                return frozenset(roles)

    def get_account_scope(self, account_id: UUID | str) -> Scope:
        aid = _parse_uuid(account_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return Scope()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.brand_ids, s.region_ids, s.store_ids, s.assigned_area_ids,
                           s.heat_zone_ids, s.modules, s.clearance, a.tenant_id
                    FROM identity.account_scopes s
                    JOIN identity.accounts a ON s.account_id = a.account_id
                    WHERE s.account_id = %s
                    """,
                    (str(aid),),
                )
                row = cur.fetchone()
                if not row:
                    # Resolve the tenant on the connection already held: going
                    # back to the pool here would hold two connections for one
                    # read and can deadlock a small pool.
                    cur.execute(
                        "SELECT tenant_id FROM identity.accounts WHERE account_id = %s",
                        (str(aid),),
                    )
                    account_row = cur.fetchone()
                    tenant_id = str(account_row[0]) if account_row else None
                    return Scope(tenant_id=tenant_id, clearance=DataClassification.CONFIDENTIAL)

                def _parse_list(val: Any) -> frozenset[str]:
                    if isinstance(val, list):
                        return frozenset(str(x) for x in val if x)
                    return frozenset()

                try:
                    clearance = DataClassification[str(row[6]).upper()]
                except (KeyError, AttributeError):
                    clearance = DataClassification.CONFIDENTIAL

                return Scope(
                    tenant_id=str(row[7]) if row[7] else None,
                    brand_ids=_parse_list(row[0]),
                    region_ids=_parse_list(row[1]),
                    store_ids=_parse_list(row[2]),
                    assigned_area_ids=_parse_list(row[3]),
                    heat_zone_ids=_parse_list(row[4]),
                    modules=_parse_list(row[5]),
                    clearance=clearance,
                )

    def save_account(self, account: Account) -> None:
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO identity.accounts (
                        account_id, tenant_id, username, email, display_name, status,
                        created_at, created_by, updated_at, disabled_at, disabled_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (account_id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        username = EXCLUDED.username,
                        email = EXCLUDED.email,
                        display_name = EXCLUDED.display_name,
                        status = EXCLUDED.status,
                        updated_at = now(),
                        disabled_at = EXCLUDED.disabled_at,
                        disabled_reason = EXCLUDED.disabled_reason
                    """,
                    (
                        str(account.account_id),
                        str(account.tenant_id),
                        account.username,
                        account.email,
                        account.display_name,
                        account.status,
                        account.created_at or datetime.now(UTC),
                        account.created_by,
                        account.updated_at or datetime.now(UTC),
                        account.disabled_at,
                        account.disabled_reason,
                    ),
                )
                conn.commit()

    def set_account_roles(self, account_id: UUID | str, roles: Iterable[Role | str]) -> None:
        aid = _parse_uuid(account_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM identity.account_roles WHERE account_id = %s",
                    (str(aid),),
                )
                for r in roles:
                    role_val = r.value if isinstance(r, Role) else str(r)
                    cur.execute(
                        """
                        INSERT INTO identity.account_roles (account_id, role, granted_at, granted_by)
                        VALUES (%s, %s, now(), 'system')
                        """,
                        (str(aid), role_val),
                    )
                conn.commit()

    def set_account_scope(self, account_id: UUID | str, scope: Scope) -> None:
        aid = _parse_uuid(account_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return
            import json

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO identity.account_scopes (
                        account_id, brand_ids, region_ids, store_ids, assigned_area_ids,
                        heat_zone_ids, modules, clearance
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (account_id) DO UPDATE SET
                        brand_ids = EXCLUDED.brand_ids,
                        region_ids = EXCLUDED.region_ids,
                        store_ids = EXCLUDED.store_ids,
                        assigned_area_ids = EXCLUDED.assigned_area_ids,
                        heat_zone_ids = EXCLUDED.heat_zone_ids,
                        modules = EXCLUDED.modules,
                        clearance = EXCLUDED.clearance
                    """,
                    (
                        str(aid),
                        json.dumps(sorted(scope.brand_ids)),
                        json.dumps(sorted(scope.region_ids)),
                        json.dumps(sorted(scope.store_ids)),
                        json.dumps(sorted(scope.assigned_area_ids)),
                        json.dumps(sorted(scope.heat_zone_ids)),
                        json.dumps(sorted(scope.modules)),
                        scope.clearance.name,
                    ),
                )
                conn.commit()

    def link_federated_identity(
        self,
        account_id: UUID | str,
        issuer: str,
        subject: str,
        linked_by: str = "system",
    ) -> None:
        aid = _parse_uuid(account_id)
        with open_connection(self._conn_factory) as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO identity.federated_identities (account_id, issuer, subject, linked_at, linked_by)
                    VALUES (%s, %s, %s, now(), %s)
                    ON CONFLICT (issuer, subject) DO UPDATE SET
                        account_id = EXCLUDED.account_id,
                        linked_at = now(),
                        linked_by = EXCLUDED.linked_by
                    """,
                    (str(aid), issuer, subject, linked_by),
                )
                conn.commit()
