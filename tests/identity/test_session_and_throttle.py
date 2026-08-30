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
    account_attempt_key,
    ip_attempt_key,
)
from shared.identity.session_service import (
    RevocationReason,
    Session,
    SessionConfig,
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
        for _i in range(4):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        result = self.svc.check_account(self.account_id, now=now)
        assert result.allowed is True

    def test_account_locked_after_5_failures(self) -> None:
        """每帳號 15 分鐘內 5 次失敗 → 鎖定。"""
        now = datetime.now(UTC)
        for _i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        result = self.svc.check_account(self.account_id, now=now)
        assert result.allowed is False
        assert result.locked_until is not None
        assert result.reason == "account_locked"

    def test_lockout_duration_is_15_minutes(self) -> None:
        """基礎鎖定時間為 15 分鐘。"""
        now = datetime.now(UTC)
        for _i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        result = self.svc.check_account(self.account_id, now=now)
        assert result.locked_until is not None
        lockout_duration = result.locked_until - now
        assert lockout_duration == timedelta(minutes=15)

    def test_lockout_expires(self) -> None:
        """鎖定到期後恢復允許。"""
        now = datetime.now(UTC)
        for _i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        # 15 分鐘後
        after_lockout = now + timedelta(minutes=16)
        result = self.svc.check_account(self.account_id, now=after_lockout)
        assert result.allowed is True

    def test_success_clears_account_count(self) -> None:
        """成功登入清除該帳號計數。"""
        now = datetime.now(UTC)
        for _i in range(3):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        self.svc.record_success(self.account_id)

        # 帳號記錄應該被清除
        record = self.repo.get(account_attempt_key(self.account_id))
        assert record is None

    def test_ip_blocking_after_50_failures(self) -> None:
        """每來源 IP 15 分鐘內 50 次失敗 → 拒絕（Contract §6.4）。"""
        now = datetime.now(UTC)
        # 用不同帳號來避免帳號鎖定
        for _i in range(50):
            acct = str(uuid4())
            self.svc.record_failure(acct, self.ip, now=now)

        result = self.svc.check_ip(self.ip, now=now)
        assert result.allowed is False, (
            "IP 超過 50 次失敗後必須拒絕（Contract §6.4: 拒絕並記錄）"
        )
        assert result.reason == "ip_blocked"

    def test_window_expiry_resets_count(self) -> None:
        """視窗過期重置計數。"""
        now = datetime.now(UTC)
        for _i in range(4):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        # 16 分鐘後（超過 15 分鐘視窗）
        after_window = now + timedelta(minutes=16)
        result = self.svc.check_account(self.account_id, now=after_window)
        assert result.allowed is True

    def _drive_lock_round(self, at: datetime) -> datetime:
        """在 `at` 走完一輪「先 check 再 record」的失敗流程，回傳鎖定結束時間。

        這是正確呼叫端的模式：被拒絕就不再送出憑證驗證，因此不會有
        「鎖定期間繼續累積失敗次數」的副作用。
        """
        for _i in range(self.svc.config.account_max_failures):
            precheck = self.svc.check_account(self.account_id, now=at)
            assert precheck.allowed is True, "未達門檻前 check_account 必須放行"
            self.svc.record_failure(self.account_id, self.ip, now=at)

        result = self.svc.check_account(self.account_id, now=at)
        assert result.allowed is False
        assert result.locked_until is not None
        return result.locked_until

    def test_exponential_backoff_across_lock_rounds(self) -> None:
        """迴歸測試：check-first 呼叫端連續鎖定必須加倍（Contract §6.4）。

        每輪鎖定結束後計數視窗也已過期，若沒有跨視窗的「已鎖定輪次」狀態，
        每一輪都會退回 15 分鐘基礎鎖定，「每次再鎖定加倍」永遠不會發生。
        """
        at = datetime.now(UTC)
        durations: list[timedelta] = []

        for _round in range(4):
            locked_until = self._drive_lock_round(at)
            durations.append(locked_until - at)
            # 鎖定結束後 1 分鐘再來一輪（此時視窗也已過期）
            at = locked_until + timedelta(minutes=1)

        assert durations == [
            timedelta(minutes=15),
            timedelta(minutes=30),
            timedelta(minutes=60),
            timedelta(minutes=60),
        ], f"連續鎖定時長應為 15m/30m/60m/60m（上限 60m），實際 {durations}"

    def test_lock_survives_record_failure_during_lockout(self) -> None:
        """迴歸測試：鎖定期間再記一次失敗，不得抹除 locked_until。

        `_increment()` 若在視窗過期時重建記錄，60 分鐘鎖定會在 t0+16m 消失。
        """
        at = datetime.now(UTC)
        round_start = at
        locked_until = at
        for _round in range(3):
            round_start = at
            locked_until = self._drive_lock_round(at)
            at = locked_until + timedelta(minutes=1)

        # 第三輪為 60 分鐘鎖定
        assert locked_until - round_start == timedelta(minutes=60)

        # 鎖定期間、且計數視窗（15 分鐘）已過期時再記一次失敗
        during_lock = round_start + timedelta(minutes=16)
        self.svc.record_failure(self.account_id, self.ip, now=during_lock)

        result = self.svc.check_account(self.account_id, now=during_lock)
        assert result.allowed is False, (
            "鎖定期間的失敗記錄不得清掉 60 分鐘鎖定（視窗過期不等於鎖定過期）"
        )
        assert result.locked_until == locked_until, (
            "鎖定期間再失敗不應重設鎖定結束時間"
        )

        # 60 分鐘鎖定仍完整走到底
        at_59m = round_start + timedelta(minutes=59)
        assert self.svc.check_account(self.account_id, now=at_59m).allowed is False
        at_61m = round_start + timedelta(minutes=61)
        assert self.svc.check_account(self.account_id, now=at_61m).allowed is True

    def test_lockout_not_extended_by_repeated_failures_in_same_round(self) -> None:
        """同一輪鎖定內連續失敗不得重複加倍（加倍單位是鎖定輪次）。"""
        now = datetime.now(UTC)
        for _i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        first = self.svc.check_account(self.account_id, now=now)
        assert first.locked_until is not None
        assert first.locked_until - now == timedelta(minutes=15)

        # 鎖定期間再失敗 3 次
        for _i in range(3):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        second = self.svc.check_account(self.account_id, now=now)
        assert second.locked_until == first.locked_until, (
            "同一輪鎖定內的額外失敗不應改變鎖定結束時間"
        )

    def test_lockout_escalation_decays_after_retention(self) -> None:
        """超過保留期後退避輪次歸零，且不依賴呼叫端有沒有先走 check。"""
        now = datetime.now(UTC)
        first_lock = self._drive_lock_round(now)
        assert first_lock - now == timedelta(minutes=15)

        # 只呼叫 record_failure（不 check），時間拉到保留期之外
        far = first_lock + self.svc.config.lockout_retention + timedelta(minutes=20)
        for _i in range(self.svc.config.account_max_failures):
            self.svc.record_failure(self.account_id, self.ip, now=far)

        result = self.svc.check_account(self.account_id, now=far)
        assert result.locked_until is not None
        assert result.locked_until - far == timedelta(minutes=15), (
            "保留期外的舊鎖定紀錄不應讓下一輪直接跳到 30 分鐘"
        )

    def test_success_clears_lockout_escalation(self) -> None:
        """成功登入清除整筆記錄，退避輪次一併歸零（Contract §6.4）。"""
        at = datetime.now(UTC)
        first_lock = self._drive_lock_round(at)
        assert first_lock - at == timedelta(minutes=15)

        self.svc.record_success(self.account_id)
        assert self.repo.get(account_attempt_key(self.account_id)) is None

        # 歸零後下一輪應回到基礎鎖定時間
        next_round = first_lock + timedelta(minutes=1)
        again = self._drive_lock_round(next_round)
        assert again - next_round == timedelta(minutes=15)

    def test_ip_49_failures_still_allowed(self) -> None:
        """迴歸測試：IP 49 次失敗仍允許，50 次才拒絕。"""
        now = datetime.now(UTC)
        for _i in range(49):
            acct = str(uuid4())
            self.svc.record_failure(acct, self.ip, now=now)

        result = self.svc.check_ip(self.ip, now=now)
        assert result.allowed is True, "49 次失敗不應觸發 IP 阻擋"

    def test_account_locked_check_returns_false(self) -> None:
        """迴歸測試：帳號鎖定後 check_account 必須回傳 allowed=False。"""
        now = datetime.now(UTC)
        for _i in range(5):
            self.svc.record_failure(self.account_id, self.ip, now=now)

        result = self.svc.check_account(self.account_id, now=now)
        assert result.allowed is False
        assert result.reason == "account_locked"


# ============================================================================
# T05b: attempt_key 只存帳號鍵或來源 IP 雜湊（Contract §2.2）
# ============================================================================

class TestAttemptKeyHashing:
    """驗證 identity.login_attempts 不會落地明文 client IP。"""

    IP_V4 = "203.0.113.42"

    def test_ip_attempt_key_contains_no_plaintext_ip(self) -> None:
        """attempt_key 不得包含原始 IP 字串（Contract §2.2）。"""
        key = ip_attempt_key(self.IP_V4)

        assert self.IP_V4 not in key, "attempt_key 不得含明文 IP"
        assert key.startswith("ip:")
        digest = key.split(":", 1)[1]
        assert len(digest) == 64, "SHA-256 hex digest 長度應為 64"
        assert all(c in "0123456789abcdef" for c in digest)
        # 任一 octet 也不得以原樣出現
        for octet in self.IP_V4.split("."):
            assert f".{octet}." not in key

    def test_ip_attempt_key_is_deterministic_and_distinct(self) -> None:
        """同一 IP 必須對到同一 key，不同 IP 必須分開計數。"""
        assert ip_attempt_key(self.IP_V4) == ip_attempt_key(self.IP_V4)
        assert ip_attempt_key(self.IP_V4) != ip_attempt_key("203.0.113.43")

    def test_ip_attempt_key_normalizes_equivalent_forms(self) -> None:
        """等價的 IPv6 表述必須正規化成同一個 key，避免繞過節流。"""
        assert ip_attempt_key("2001:DB8::1") == ip_attempt_key(
            "2001:0db8:0000:0000:0000:0000:0000:0001"
        )
        assert ip_attempt_key(" 203.0.113.42 ") == ip_attempt_key(self.IP_V4)

    def test_ip_attempt_key_pepper_changes_digest(self) -> None:
        """加上 deployment-scoped pepper 後改用 HMAC，摘要必須不同。"""
        plain = ip_attempt_key(self.IP_V4)
        peppered = ip_attempt_key(self.IP_V4, pepper="deployment-secret")

        assert peppered != plain
        assert self.IP_V4 not in peppered
        assert peppered == ip_attempt_key(self.IP_V4, pepper="deployment-secret")
        assert peppered != ip_attempt_key(self.IP_V4, pepper="other-secret")

    def test_persisted_keys_contain_no_plaintext_ip(self) -> None:
        """實際寫入 repository 的 key 不得含明文 IP。"""
        repo = InMemoryThrottleRepository()
        svc = LoginThrottleService(repo)
        svc.record_failure(str(uuid4()), self.IP_V4, now=datetime.now(UTC))

        stored_keys = list(repo._store)
        assert stored_keys, "record_failure 應寫入節流記錄"
        for key in stored_keys:
            assert self.IP_V4 not in key

    def test_service_pepper_is_used_for_lookup(self) -> None:
        """服務層帶 pepper 時，寫入與檢查必須使用同一組雜湊 key。"""
        repo = InMemoryThrottleRepository()
        svc = LoginThrottleService(repo, ip_pepper="deployment-secret")
        now = datetime.now(UTC)

        for _i in range(svc.config.ip_max_failures):
            svc.record_failure(str(uuid4()), self.IP_V4, now=now)

        assert svc.check_ip(self.IP_V4, now=now).allowed is False
        assert ip_attempt_key(self.IP_V4, pepper="deployment-secret") in repo._store
        # 未加 pepper 的 key 不應存在
        assert ip_attempt_key(self.IP_V4) not in repo._store


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
