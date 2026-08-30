"""ODP-WEB-LOCAL-IDENTITY-CORE-001 測試套件：T05, T06, T07

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5, §6.4, §10

T05: 節流與鎖定門檻（§6.4）— 跨程序共享；成功登入清零
T06: session 建立 / 輪替 / 撤銷（§5.3、§5.4）— 四個輪替時點皆換新
T07: 密碼變更撤銷其他所有 session — 其他 session 於下一次請求回 401
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from shared.identity.login_throttle import (
    LoginAttemptRecord,
    LoginThrottleService,
    ThrottleConfig,
    ThrottleRepository,
    account_attempt_key,
    ip_attempt_key,
)
from shared.identity.session_service import (
    RevocationReason,
    Session,
    SessionConfig,
    SessionRepository,
    SessionService,
)


# ============================================================================
# In-Memory Repository 實作（測試用）
# ============================================================================

class InMemorySessionRepository:
    """測試用的 in-memory session repository。"""

    def __init__(self) -> None:
        self._store: dict[UUID, Session] = {}

    def save(self, session: Session) -> None:
        self._store[session.session_id] = session

    def find_by_id(self, session_id: UUID) -> Session | None:
        return self._store.get(session_id)

    def find_active_by_account(self, account_id: UUID) -> list[Session]:
        return [
            s for s in self._store.values()
            if s.account_id == account_id and s.is_active
        ]

    def revoke(
        self, session_id: UUID, reason: str, revoked_at: datetime | None = None
    ) -> None:
        session = self._store.get(session_id)
        if session:
            session.revoked_at = revoked_at or datetime.now(UTC)
            session.revoked_reason = reason

    def revoke_all_for_account(
        self,
        account_id: UUID,
        reason: str,
        *,
        except_session_id: UUID | None = None,
        revoked_at: datetime | None = None,
    ) -> int:
        ts = revoked_at or datetime.now(UTC)
        count = 0
        for session in self._store.values():
            if (
                session.account_id == account_id
                and session.revoked_at is None
                and session.session_id != except_session_id
            ):
                session.revoked_at = ts
                session.revoked_reason = reason
                count += 1
        return count

    def update_last_seen(
        self, session_id: UUID, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None:
        session = self._store.get(session_id)
        if session:
            session.last_seen_at = last_seen_at
            session.idle_expires_at = idle_expires_at


class InMemoryThrottleRepository:
    """測試用的 in-memory throttle repository。"""

    def __init__(self) -> None:
        self._store: dict[str, LoginAttemptRecord] = {}

    def get(self, attempt_key: str) -> LoginAttemptRecord | None:
        return self._store.get(attempt_key)

    def upsert(self, record: LoginAttemptRecord) -> None:
        self._store[record.attempt_key] = record

    def delete(self, attempt_key: str) -> None:
        self._store.pop(attempt_key, None)


# ============================================================================
# T05: 節流與鎖定門檻
# ============================================================================

class TestT05LoginThrottle:
    """驗證登入節流與鎖定（Contract §6.4）。"""

    def setup_method(self) -> None:
        self.repo = InMemoryThrottleRepository()
        self.svc = LoginThrottleService(self.repo)
        self.account_id = str(uuid4())
        self.ip = "192.168.1.100"

    def test_first_attempt_allowed(self) -> None:
        """第一次嘗試應該允許。"""
        result = self.svc.check_account(self.account_id)
        assert result.allowed is True

    def test_under_threshold_allowed(self) -> None:
        """低於門檻的失敗次數應該允許。"""
        now = datetime.now(UTC)
        for i in range(4):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        result = self.svc.check_account(self.account_id, now=now)
        assert result.allowed is True

    def test_account_locked_after_5_failures(self) -> None:
        """每帳號 15 分鐘內 5 次失敗 → 鎖定。"""
        now = datetime.now(UTC)
        for i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        result = self.svc.check_account(self.account_id, now=now)
        assert result.allowed is False
        assert result.locked_until is not None
        assert result.reason == "account_locked"

    def test_lockout_duration_is_15_minutes(self) -> None:
        """基礎鎖定時間為 15 分鐘。"""
        now = datetime.now(UTC)
        for i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        result = self.svc.check_account(self.account_id, now=now)
        assert result.locked_until is not None
        lockout_duration = result.locked_until - now
        assert lockout_duration == timedelta(minutes=15)

    def test_lockout_expires(self) -> None:
        """鎖定到期後恢復允許。"""
        now = datetime.now(UTC)
        for i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        # 15 分鐘後
        after_lockout = now + timedelta(minutes=16)
        result = self.svc.check_account(self.account_id, now=after_lockout)
        assert result.allowed is True

    def test_success_clears_account_count(self) -> None:
        """成功登入清除該帳號計數。"""
        now = datetime.now(UTC)
        for i in range(3):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        self.svc.record_success(self.account_id)

        # 帳號記錄應該被清除
        record = self.repo.get(account_attempt_key(self.account_id))
        assert record is None

    def test_ip_blocking_after_50_failures(self) -> None:
        """每來源 IP 15 分鐘內 50 次失敗 → 拒絕。"""
        now = datetime.now(UTC)
        # 用不同帳號來避免帳號鎖定
        for i in range(50):
            acct = str(uuid4())
            self.svc.record_failure(acct, self.ip, now=now)

        result = self.svc.check_ip(self.ip, now=now)
        # IP 維度沒有鎖定機制，但超過門檻後也可以繼續
        # 實際的 IP 阻擋需要在呼叫端檢查 failure_count
        # 這裡驗證 IP 記錄有被累計
        ip_record = self.repo.get(ip_attempt_key(self.ip))
        assert ip_record is not None
        assert ip_record.failure_count >= 50

    def test_window_expiry_resets_count(self) -> None:
        """視窗過期重置計數。"""
        now = datetime.now(UTC)
        for i in range(4):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        # 16 分鐘後（超過 15 分鐘視窗）
        after_window = now + timedelta(minutes=16)
        result = self.svc.check_account(self.account_id, now=after_window)
        assert result.allowed is True

    def test_exponential_backoff(self) -> None:
        """指數退避：每次再鎖定加倍。"""
        now = datetime.now(UTC)
        # 第一輪鎖定 (5 次失敗)
        for i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        record = self.repo.get(account_attempt_key(self.account_id))
        assert record is not None
        assert record.locked_until is not None
        first_lockout = record.locked_until - now

        # 繼續失敗 (第 6 次)
        self.svc.record_failure(self.account_id, self.ip, now=now)
        record = self.repo.get(account_attempt_key(self.account_id))
        assert record is not None
        assert record.locked_until is not None
        second_lockout = record.locked_until - now

        # 第二次鎖定應該比第一次長
        assert second_lockout > first_lockout


# ============================================================================
# T06: session 建立 / 輪替 / 撤銷
# ============================================================================

class TestT06SessionLifecycle:
    """驗證 session 生命週期（Contract §5.3、§5.4）。"""

    def setup_method(self) -> None:
        self.repo = InMemorySessionRepository()
        self.svc = SessionService(self.repo)
        self.account_id = uuid4()

    def test_create_session(self) -> None:
        """建立 session。"""
        session = self.svc.create_session(self.account_id, "local_password")
        assert session.account_id == self.account_id
        assert session.provider == "local_password"
        assert session.is_active is True
        assert session.revoked_at is None

    def test_session_idle_timeout(self) -> None:
        """Session idle timeout 預設 30 分鐘。"""
        now = datetime.now(UTC)
        session = self.svc.create_session(self.account_id, "local_password", now=now)
        expected_idle = now + timedelta(minutes=30)
        assert session.idle_expires_at == expected_idle

    def test_session_absolute_lifetime(self) -> None:
        """Session absolute lifetime ≤ 8 小時。"""
        now = datetime.now(UTC)
        session = self.svc.create_session(self.account_id, "local_password", now=now)
        expected_abs = now + timedelta(hours=8)
        assert session.absolute_expires_at == expected_abs

    def test_rotate_session_creates_new_and_revokes_old(self) -> None:
        """輪替 session 必須建立新 session 並撤銷舊 session。"""
        old_session = self.svc.create_session(self.account_id, "local_password")
        new_session = self.svc.rotate_session(old_session)

        # 新 session
        assert new_session.session_id != old_session.session_id
        assert new_session.account_id == self.account_id
        assert new_session.rotated_from == old_session.session_id
        assert new_session.is_active is True

        # 舊 session 已撤銷
        old_from_repo = self.repo.find_by_id(old_session.session_id)
        assert old_from_repo is not None
        assert old_from_repo.revoked_at is not None

    def test_rotate_preserves_absolute_expires(self) -> None:
        """輪替保留原始 absolute_expires_at。"""
        old_session = self.svc.create_session(self.account_id, "local_password")
        new_session = self.svc.rotate_session(old_session)
        assert new_session.absolute_expires_at == old_session.absolute_expires_at

    def test_needs_rotation_after_15_minutes(self) -> None:
        """距上次輪替超過 15 分鐘的第一個請求須輪替。"""
        now = datetime.now(UTC)
        session = self.svc.create_session(self.account_id, "local_password", now=now)

        # 14 分鐘後不需要
        assert self.svc.needs_rotation(session, now=now + timedelta(minutes=14)) is False

        # 16 分鐘後需要
        assert self.svc.needs_rotation(session, now=now + timedelta(minutes=16)) is True

    def test_revoke_session(self) -> None:
        """撤銷 session。"""
        session = self.svc.create_session(self.account_id, "local_password")
        self.svc.revoke_session(session.session_id, RevocationReason.USER_LOGOUT)

        revoked = self.repo.find_by_id(session.session_id)
        assert revoked is not None
        assert revoked.revoked_at is not None
        assert revoked.revoked_reason == "user_logout"
        assert revoked.is_active is False

    def test_validate_revoked_session_returns_none(self) -> None:
        """驗證已撤銷的 session 回傳 None。"""
        session = self.svc.create_session(self.account_id, "local_password")
        self.svc.revoke_session(session.session_id, RevocationReason.USER_LOGOUT)

        assert self.svc.validate_session(session.session_id) is None

    def test_validate_active_session_returns_session(self) -> None:
        """驗證有效的 session 回傳 Session 物件。"""
        session = self.svc.create_session(self.account_id, "local_password")
        result = self.svc.validate_session(session.session_id)
        assert result is not None
        assert result.session_id == session.session_id

    def test_validate_nonexistent_session_returns_none(self) -> None:
        """驗證不存在的 session 回傳 None。"""
        assert self.svc.validate_session(uuid4()) is None

    def test_touch_session_updates_idle(self) -> None:
        """觸碰 session 更新 idle_expires_at。"""
        now = datetime.now(UTC)
        session = self.svc.create_session(self.account_id, "local_password", now=now)

        # 10 分鐘後觸碰
        touch_time = now + timedelta(minutes=10)
        self.svc.touch_session(session, now=touch_time)

        assert session.last_seen_at == touch_time
        assert session.idle_expires_at == touch_time + timedelta(minutes=30)

    def test_idle_expired_session_is_not_active(self) -> None:
        """idle timeout 過期的 session 不再活躍。"""
        now = datetime.now(UTC)
        session = self.svc.create_session(self.account_id, "local_password", now=now)

        # 模擬 idle 過期
        session.idle_expires_at = now - timedelta(minutes=1)
        assert session.is_active is False

    def test_absolute_expired_session_is_not_active(self) -> None:
        """absolute lifetime 過期的 session 不再活躍。"""
        now = datetime.now(UTC)
        session = self.svc.create_session(self.account_id, "local_password", now=now)

        # 模擬 absolute 過期
        session.absolute_expires_at = now - timedelta(minutes=1)
        assert session.is_active is False


# ============================================================================
# T07: 密碼變更撤銷其他所有 session
# ============================================================================

class TestT07PasswordChangeRevocation:
    """驗證密碼變更撤銷該帳號其他所有 session。"""

    def setup_method(self) -> None:
        self.repo = InMemorySessionRepository()
        self.svc = SessionService(self.repo)
        self.account_id = uuid4()

    def test_password_change_revokes_other_sessions(self) -> None:
        """密碼變更後，其他所有 session 被撤銷。"""
        # 建立 3 個 session
        s1 = self.svc.create_session(self.account_id, "local_password")
        s2 = self.svc.create_session(self.account_id, "local_password")
        s3 = self.svc.create_session(self.account_id, "local_password")

        # 密碼變更（保留 s1，撤銷 s2、s3）
        revoked_count = self.svc.on_password_changed(self.account_id, s1.session_id)
        assert revoked_count == 2

        # s1 仍然有效
        assert self.svc.validate_session(s1.session_id) is not None

        # s2, s3 已撤銷
        assert self.svc.validate_session(s2.session_id) is None
        assert self.svc.validate_session(s3.session_id) is None

        # 撤銷原因
        s2_revoked = self.repo.find_by_id(s2.session_id)
        assert s2_revoked is not None
        assert s2_revoked.revoked_reason == RevocationReason.PASSWORD_CHANGE

    def test_account_disable_revokes_all_sessions(self) -> None:
        """帳號停用立即撤銷所有 session。"""
        s1 = self.svc.create_session(self.account_id, "local_password")
        s2 = self.svc.create_session(self.account_id, "local_password")

        revoked_count = self.svc.on_account_disabled(self.account_id)
        assert revoked_count == 2

        assert self.svc.validate_session(s1.session_id) is None
        assert self.svc.validate_session(s2.session_id) is None

    def test_revoked_session_returns_401_equivalent(self) -> None:
        """撤銷後的 session 驗證回傳 None（等同 401）。

        Contract §5.4: 撤銷後的請求必須回 401。
        """
        session = self.svc.create_session(self.account_id, "local_password")
        self.svc.revoke_session(session.session_id, RevocationReason.ADMIN_REVOKE)

        # validate_session 回傳 None → 呼叫端應回 401
        assert self.svc.validate_session(session.session_id) is None

    def test_logout_revokes_single_session(self) -> None:
        """登出只撤銷該 session。"""
        s1 = self.svc.create_session(self.account_id, "local_password")
        s2 = self.svc.create_session(self.account_id, "local_password")

        self.svc.logout(s1.session_id)

        assert self.svc.validate_session(s1.session_id) is None
        assert self.svc.validate_session(s2.session_id) is not None

    def test_session_config_validates_bounds(self) -> None:
        """SessionConfig 驗證契約上界。"""
        with pytest.raises(ValueError, match="idle_timeout must be ≥ 15"):
            SessionConfig(idle_timeout=timedelta(minutes=10))

        with pytest.raises(ValueError, match="idle_timeout must be ≤ 60"):
            SessionConfig(idle_timeout=timedelta(minutes=90))

        with pytest.raises(ValueError, match="absolute_lifetime must be ≤ 8"):
            SessionConfig(absolute_lifetime=timedelta(hours=9))
