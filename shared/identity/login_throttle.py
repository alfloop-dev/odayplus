"""登入節流與鎖定服務。

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §6.4
- 每帳號：15 分鐘內 5 次失敗 → 鎖定 15 分鐘，指數退避（上限 60 分鐘）
- 每來源 IP：15 分鐘內 50 次失敗 → 拒絕並記錄
- 成功登入 → 清除該帳號計數
- 節流狀態持久化在 identity.login_attempts（Cloud Run 多實例共享）
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Protocol


# ────────────────────────────────────────────────────────────────────────────
# 節流參數（Contract §6.4）
# ────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ThrottleConfig:
    """登入節流可調參數。"""

    window: timedelta = timedelta(minutes=15)
    """計數視窗"""

    account_max_failures: int = 5
    """每帳號最大失敗次數"""

    ip_max_failures: int = 50
    """每來源 IP 最大失敗次數"""

    base_lockout: timedelta = timedelta(minutes=15)
    """基礎鎖定時間"""

    max_lockout: timedelta = timedelta(minutes=60)
    """最大鎖定時間（指數退避上限）"""


# ────────────────────────────────────────────────────────────────────────────
# 節流記錄
# ────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class LoginAttemptRecord:
    """對應 identity.login_attempts 表。"""

    attempt_key: str
    window_started_at: datetime
    failure_count: int = 0
    locked_until: datetime | None = None


class ThrottleResult:
    """節流檢查結果。"""

    def __init__(
        self,
        allowed: bool,
        *,
        locked_until: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        self.allowed = allowed
        self.locked_until = locked_until
        self.reason = reason


# ────────────────────────────────────────────────────────────────────────────
# 節流 Repository Protocol
# ────────────────────────────────────────────────────────────────────────────

class ThrottleRepository(Protocol):
    """登入節流持久化介面。"""

    def get(self, attempt_key: str) -> LoginAttemptRecord | None:
        """取得節流記錄。"""
        ...

    def upsert(self, record: LoginAttemptRecord) -> None:
        """新增或更新節流記錄。"""
        ...

    def delete(self, attempt_key: str) -> None:
        """清除節流記錄。"""
        ...


# ────────────────────────────────────────────────────────────────────────────
# Attempt Key 建構
# ────────────────────────────────────────────────────────────────────────────

def account_attempt_key(account_id: str) -> str:
    """帳號維度的 attempt_key。"""
    return f"account:{account_id}"


def ip_attempt_key(ip_address: str) -> str:
    """來源 IP 維度的 attempt_key。"""
    return f"ip:{ip_address}"


# ────────────────────────────────────────────────────────────────────────────
# 登入節流服務
# ────────────────────────────────────────────────────────────────────────────

class LoginThrottleService:
    """登入節流與鎖定業務邏輯（Contract §6.4）。"""

    def __init__(
        self,
        repository: ThrottleRepository,
        config: ThrottleConfig | None = None,
    ) -> None:
        self._repo = repository
        self._config = config or ThrottleConfig()

    @property
    def config(self) -> ThrottleConfig:
        return self._config

    # ── 檢查是否允許嘗試 ──────────────────────────────────────────────────

    def check_account(
        self, account_id: str, *, now: datetime | None = None
    ) -> ThrottleResult:
        """檢查帳號維度的節流狀態。"""
        return self._check(
            account_attempt_key(account_id),
            self._config.account_max_failures,
            now=now,
        )

    def check_ip(
        self, ip_address: str, *, now: datetime | None = None
    ) -> ThrottleResult:
        """檢查 IP 維度的節流狀態。"""
        return self._check(
            ip_attempt_key(ip_address),
            self._config.ip_max_failures,
            now=now,
        )

    # ── 記錄失敗 ──────────────────────────────────────────────────────────

    def record_failure(
        self, account_id: str, ip_address: str, *, now: datetime | None = None
    ) -> None:
        """記錄一次登入失敗。

        同時更新帳號與 IP 兩個維度。
        """
        ts = now or datetime.now(UTC)
        self._increment(account_attempt_key(account_id), ts)
        self._increment(ip_attempt_key(ip_address), ts)

        # 檢查帳號是否需要鎖定
        record = self._repo.get(account_attempt_key(account_id))
        if record and record.failure_count >= self._config.account_max_failures:
            lockout = self._calculate_lockout(record.failure_count)
            record.locked_until = ts + lockout
            self._repo.upsert(record)

    # ── 記錄成功（清除帳號計數） ──────────────────────────────────────────

    def record_success(self, account_id: str) -> None:
        """成功登入 → 清除該帳號計數（Contract §6.4）。"""
        self._repo.delete(account_attempt_key(account_id))

    # ── 內部 ──────────────────────────────────────────────────────────────

    def _check(
        self,
        attempt_key: str,
        max_failures: int,
        *,
        now: datetime | None = None,
    ) -> ThrottleResult:
        ts = now or datetime.now(UTC)
        record = self._repo.get(attempt_key)

        if record is None:
            return ThrottleResult(allowed=True)

        # 視窗過期 → 重置
        if ts - record.window_started_at > self._config.window:
            self._repo.delete(attempt_key)
            return ThrottleResult(allowed=True)

        # 鎖定中？
        if record.locked_until and ts < record.locked_until:
            return ThrottleResult(
                allowed=False,
                locked_until=record.locked_until,
                reason="account_locked" if "account:" in attempt_key else "ip_blocked",
            )

        # 超過門檻（但鎖定已過期）？
        if record.failure_count >= max_failures:
            # 鎖定已過期，允許但保留記錄
            return ThrottleResult(allowed=True)

        return ThrottleResult(allowed=True)

    def _increment(self, attempt_key: str, now: datetime) -> None:
        record = self._repo.get(attempt_key)

        if record is None:
            record = LoginAttemptRecord(
                attempt_key=attempt_key,
                window_started_at=now,
                failure_count=1,
            )
        elif now - record.window_started_at > self._config.window:
            # 視窗過期，重新開始
            record = LoginAttemptRecord(
                attempt_key=attempt_key,
                window_started_at=now,
                failure_count=1,
            )
        else:
            record.failure_count += 1

        self._repo.upsert(record)

    def _calculate_lockout(self, failure_count: int) -> timedelta:
        """指數退避鎖定時間（每次再鎖定加倍，上限 60 分鐘）。"""
        # 第一次超過門檻 → base_lockout
        # 後續每次加倍
        excess = max(0, failure_count - self._config.account_max_failures)
        lockout_seconds = self._config.base_lockout.total_seconds() * (2 ** excess)
        max_seconds = self._config.max_lockout.total_seconds()
        return timedelta(seconds=min(lockout_seconds, max_seconds))
