"""持久 session 管理服務。

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5
- Session 建立、輪替、撤銷、idle/absolute 到期
- Cookie 只承載不透明 session 參照
- 權威 session 狀態在 identity.sessions

本模組實作 session 的業務邏輯層，不直接操作資料庫。
實際持久化由 SessionRepository (Protocol) 負責。
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4


# ────────────────────────────────────────────────────────────────────────────
# Session 生命週期參數（Contract §5.2）
# ────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SessionConfig:
    """Session 生命週期可調參數。"""

    idle_timeout: timedelta = timedelta(minutes=30)
    """Idle timeout：預設 30 分鐘（可設定 15–60）"""

    absolute_lifetime: timedelta = timedelta(hours=8)
    """Absolute lifetime：≤ 8 小時"""

    rotation_interval: timedelta = timedelta(minutes=15)
    """距上次輪替超過此時間的第一個請求須輪替"""

    def __post_init__(self) -> None:
        # 驗證契約上界
        if self.idle_timeout < timedelta(minutes=15):
            raise ValueError("idle_timeout must be ≥ 15 minutes")
        if self.idle_timeout > timedelta(minutes=60):
            raise ValueError("idle_timeout must be ≤ 60 minutes")
        if self.absolute_lifetime > timedelta(hours=8):
            raise ValueError("absolute_lifetime must be ≤ 8 hours")


# ────────────────────────────────────────────────────────────────────────────
# Session 資料模型
# ────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Session:
    """持久 session 記錄（對應 identity.sessions 表）。"""

    session_id: UUID
    account_id: UUID
    provider: str  # 'local_password' | 'oidc'
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    rotated_from: UUID | None = None

    @property
    def is_active(self) -> bool:
        """Session 是否仍有效。"""
        if self.revoked_at is not None:
            return False
        now = datetime.now(UTC)
        return now < self.idle_expires_at and now < self.absolute_expires_at


# ────────────────────────────────────────────────────────────────────────────
# Session Repository Protocol
# ────────────────────────────────────────────────────────────────────────────

class SessionRepository(Protocol):
    """Session 持久化介面。

    實作者須保證 identity.sessions 表操作的交易安全性。
    """

    def save(self, session: Session) -> None:
        """儲存新 session 或更新既有 session。"""
        ...

    def find_by_id(self, session_id: UUID) -> Session | None:
        """以 session_id 查找 session。"""
        ...

    def find_active_by_account(self, account_id: UUID) -> list[Session]:
        """查找帳號的所有活躍 session。"""
        ...

    def revoke(
        self, session_id: UUID, reason: str, revoked_at: datetime | None = None
    ) -> None:
        """撤銷單一 session。"""
        ...

    def revoke_all_for_account(
        self,
        account_id: UUID,
        reason: str,
        *,
        except_session_id: UUID | None = None,
        revoked_at: datetime | None = None,
    ) -> int:
        """撤銷帳號的所有 session（可排除指定 session）。

        Returns: 被撤銷的 session 數量
        """
        ...

    def update_last_seen(
        self, session_id: UUID, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None:
        """更新 last_seen_at 與滑動 idle_expires_at。"""
        ...


# ────────────────────────────────────────────────────────────────────────────
# Session 撤銷原因常數
# ────────────────────────────────────────────────────────────────────────────

class RevocationReason:
    """撤銷原因常數（供稽核事件使用）。"""

    USER_LOGOUT = "user_logout"
    ADMIN_DISABLE = "admin_disable_account"
    PASSWORD_CHANGE = "password_change"
    ADMIN_REVOKE = "admin_manual_revoke"
    IDLE_TIMEOUT = "idle_timeout"
    ABSOLUTE_EXPIRED = "absolute_expired"
    SESSION_ROTATION = "session_rotation"


# ────────────────────────────────────────────────────────────────────────────
# Session 服務
# ────────────────────────────────────────────────────────────────────────────

class SessionService:
    """Session 管理業務邏輯（Contract §5）。

    本服務不直接存取資料庫，所有持久化操作透過 SessionRepository。
    """

    def __init__(
        self,
        repository: SessionRepository,
        config: SessionConfig | None = None,
    ) -> None:
        self._repo = repository
        self._config = config or SessionConfig()

    @property
    def config(self) -> SessionConfig:
        return self._config

    # ── 建立 session ──────────────────────────────────────────────────────

    def create_session(
        self,
        account_id: UUID,
        provider: str,
        *,
        now: datetime | None = None,
    ) -> Session:
        """建立新 session。

        Contract §5.3.1: 登入成功時建立新 session。
        """
        ts = now or datetime.now(UTC)
        session = Session(
            session_id=uuid4(),
            account_id=account_id,
            provider=provider,
            created_at=ts,
            last_seen_at=ts,
            idle_expires_at=ts + self._config.idle_timeout,
            absolute_expires_at=ts + self._config.absolute_lifetime,
        )
        self._repo.save(session)
        return session

    # ── 輪替 session（Contract §5.3）────────────────────────────────────

    def rotate_session(
        self,
        current_session: Session,
        reason: str = RevocationReason.SESSION_ROTATION,
        *,
        now: datetime | None = None,
    ) -> Session:
        """輪替 session：撤銷舊 session 並建立新 session。

        Contract §5.3: 新列 + rotated_from 指向舊列 + 舊列標記 revoked_at。
        回應必須以新值覆寫 cookie。

        必須在以下時點呼叫：
        1. 登入成功（防 session fixation）
        2. 密碼變更成功
        3. 角色或 scope 變更
        4. 距上次輪替超過 15 分鐘的第一個請求
        """
        ts = now or datetime.now(UTC)

        # 撤銷舊 session
        self._repo.revoke(current_session.session_id, reason, ts)

        # 建立新 session，保留原始 absolute_expires_at
        new_session = Session(
            session_id=uuid4(),
            account_id=current_session.account_id,
            provider=current_session.provider,
            created_at=ts,
            last_seen_at=ts,
            idle_expires_at=ts + self._config.idle_timeout,
            absolute_expires_at=current_session.absolute_expires_at,
            rotated_from=current_session.session_id,
        )
        self._repo.save(new_session)
        return new_session

    # ── 檢查是否需要輪替 ──────────────────────────────────────────────────

    def needs_rotation(self, session: Session, *, now: datetime | None = None) -> bool:
        """檢查 session 是否需要定時輪替（§5.3.4）。"""
        ts = now or datetime.now(UTC)
        age_since_creation_or_rotation = ts - session.created_at
        return age_since_creation_or_rotation >= self._config.rotation_interval

    # ── 驗證 session ──────────────────────────────────────────────────────

    def validate_session(self, session_id: UUID) -> Session | None:
        """驗證 session 是否有效。

        Returns:
            有效的 Session 物件，或 None（已撤銷/已過期/不存在）
        """
        session = self._repo.find_by_id(session_id)
        if session is None:
            return None
        if not session.is_active:
            return None
        return session

    # ── 觸碰 session（滑動到期）────────────────────────────────────────

    def touch_session(
        self, session: Session, *, now: datetime | None = None
    ) -> Session:
        """更新 last_seen_at 與滑動 idle_expires_at。

        Contract §5.2: 每次成功請求更新 last_seen_at。
        """
        ts = now or datetime.now(UTC)
        new_idle = ts + self._config.idle_timeout
        self._repo.update_last_seen(session.session_id, ts, new_idle)
        session.last_seen_at = ts
        session.idle_expires_at = new_idle
        return session

    # ── 撤銷 ──────────────────────────────────────────────────────────────

    def revoke_session(
        self,
        session_id: UUID,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """撤銷單一 session（Contract §5.4）。"""
        ts = now or datetime.now(UTC)
        self._repo.revoke(session_id, reason, ts)

    def revoke_all_for_account(
        self,
        account_id: UUID,
        reason: str,
        *,
        except_session_id: UUID | None = None,
        now: datetime | None = None,
    ) -> int:
        """撤銷帳號的所有 session。

        Contract §5.4: 密碼變更撤銷該帳號其他所有 session。

        Returns: 被撤銷的 session 數量
        """
        ts = now or datetime.now(UTC)
        return self._repo.revoke_all_for_account(
            account_id,
            reason,
            except_session_id=except_session_id,
            revoked_at=ts,
        )

    # ── 登出 ──────────────────────────────────────────────────────────────

    def logout(self, session_id: UUID) -> None:
        """使用者主動登出。"""
        self.revoke_session(session_id, RevocationReason.USER_LOGOUT)

    # ── 帳號停用 → 撤銷所有 session ──────────────────────────────────────

    def on_account_disabled(self, account_id: UUID) -> int:
        """帳號停用時立即撤銷所有 session（Contract §7.3）。"""
        return self.revoke_all_for_account(
            account_id, RevocationReason.ADMIN_DISABLE
        )

    # ── 密碼變更 → 撤銷其他 session ──────────────────────────────────────

    def on_password_changed(
        self, account_id: UUID, current_session_id: UUID
    ) -> int:
        """密碼變更後撤銷該帳號其他所有 session（Contract §5.4）。"""
        return self.revoke_all_for_account(
            account_id,
            RevocationReason.PASSWORD_CHANGE,
            except_session_id=current_session_id,
        )
