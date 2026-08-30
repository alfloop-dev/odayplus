"""登入節流與鎖定服務。

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2, §6.4
- 每帳號：15 分鐘內 5 次失敗 → 鎖定 15 分鐘，指數退避（每次再鎖定加倍，上限 60 分鐘）
- 每來源 IP：15 分鐘內 50 次失敗 → 拒絕並記錄
- 成功登入 → 清除該帳號計數
- 節流狀態持久化在 identity.login_attempts（Cloud Run 多實例共享）
- attempt_key 只存「帳號鍵」或「來源 IP 雜湊」，**不得**落地明文 client IP（§2.2）

指數退避的倍數來自「已鎖定輪次」（`lockout_count`），而非單一視窗內的失敗次數：
計數視窗只有 15 分鐘，鎖定最長 60 分鐘，兩者跨度不同；若把倍數綁在視窗內的
失敗次數上，視窗一過期就會退回基礎鎖定時間，「每次再鎖定加倍」永遠不會發生。
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import ipaddress
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

    lockout_retention: timedelta = timedelta(hours=24)
    """鎖定升級狀態（lockout_count）的保留期。

    契約未規範退避狀態何時歸零，此為實作預設：最後一次鎖定結束後
    超過保留期即整筆清除，避免 login_attempts 無上限成長。
    帳號維度另有明確的歸零路徑——成功登入會清除整筆記錄（§6.4）。
    """


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
    lockout_count: int = 0
    """已觸發鎖定的輪次；跨計數視窗保存，供指數退避計算倍數用。"""


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

ACCOUNT_KEY_PREFIX = "account:"
IP_KEY_PREFIX = "ip:"


def account_attempt_key(account_id: str) -> str:
    """帳號維度的 attempt_key。"""
    return f"{ACCOUNT_KEY_PREFIX}{account_id}"


def normalize_ip(ip_address: str) -> str:
    """正規化來源 IP，使等價表述（大小寫、縮寫）對到同一個 key。"""
    candidate = ip_address.strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        # 非標準位址（proxy 傳來的非法值）不丟例外，改以小寫原字串計算雜湊，
        # 仍然不會落地明文——後續一律經過 SHA-256。
        return candidate.lower()


def ip_attempt_key(ip_address: str, *, pepper: str | None = None) -> str:
    """來源 IP 維度的 attempt_key（Contract §2.2：只存雜湊）。

    Args:
        ip_address: 原始 client IP。
        pepper: 選填的 deployment-scoped secret。IPv4 空間可窮舉，未加 pepper 的
            SHA-256 可被反查；有 pepper 時改用 HMAC-SHA256，使雜湊無法離線還原。

    Returns:
        ``ip:<64 hex>``，輸出**不含**任何明文 IP 片段。
    """
    normalized = normalize_ip(ip_address).encode("utf-8")
    if pepper:
        digest = hmac.new(pepper.encode("utf-8"), normalized, hashlib.sha256).hexdigest()
    else:
        digest = hashlib.sha256(normalized).hexdigest()
    return f"{IP_KEY_PREFIX}{digest}"


# ────────────────────────────────────────────────────────────────────────────
# 登入節流服務
# ────────────────────────────────────────────────────────────────────────────

class LoginThrottleService:
    """登入節流與鎖定業務邏輯（Contract §6.4）。"""

    def __init__(
        self,
        repository: ThrottleRepository,
        config: ThrottleConfig | None = None,
        *,
        ip_pepper: str | None = None,
    ) -> None:
        self._repo = repository
        self._config = config or ThrottleConfig()
        self._ip_pepper = ip_pepper

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
            self._ip_key(ip_address),
            self._config.ip_max_failures,
            now=now,
        )

    # ── 記錄失敗 ──────────────────────────────────────────────────────────

    def record_failure(
        self, account_id: str, ip_address: str, *, now: datetime | None = None
    ) -> None:
        """記錄一次登入失敗。

        同時更新帳號與 IP 兩個維度。帳號維度達門檻後設定指數退避鎖定；
        IP 維度達門檻後同樣設定鎖定以拒絕後續請求（Contract §6.4）。
        """
        ts = now or datetime.now(UTC)
        acct_key = account_attempt_key(account_id)
        ip_key = self._ip_key(ip_address)

        self._increment(acct_key, ts)
        self._increment(ip_key, ts)

        # 帳號維度：達門檻 → 指數退避鎖定（每輪鎖定加倍）
        self._apply_lockout(acct_key, self._config.account_max_failures, ts)
        # IP 維度：達門檻 → 固定鎖定（§6.4 只要求「拒絕並記錄」，不加倍）
        self._apply_lockout(ip_key, self._config.ip_max_failures, ts)

    # ── 記錄成功（清除帳號計數） ──────────────────────────────────────────

    def record_success(self, account_id: str) -> None:
        """成功登入 → 清除該帳號計數（Contract §6.4）。"""
        self._repo.delete(account_attempt_key(account_id))

    # ── 內部 ──────────────────────────────────────────────────────────────

    def _ip_key(self, ip_address: str) -> str:
        return ip_attempt_key(ip_address, pepper=self._ip_pepper)

    @staticmethod
    def _reason_for(attempt_key: str) -> str:
        return (
            "account_locked"
            if attempt_key.startswith(ACCOUNT_KEY_PREFIX)
            else "ip_blocked"
        )

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

        # ── 鎖定檢查（必須在視窗過期檢查之前） ──
        # 指數退避鎖定時間可能超過計數視窗（例如 60 分鐘鎖定 > 15 分鐘視窗），
        # 若先刪除視窗過期記錄，鎖定會被提前清掉。
        if record.locked_until and ts < record.locked_until:
            return ThrottleResult(
                allowed=False,
                locked_until=record.locked_until,
                reason=self._reason_for(attempt_key),
            )

        # 視窗過期且無有效鎖定 → 重置計數（但保留退避升級狀態）
        if ts - record.window_started_at > self._config.window:
            self._reset_window(record, ts)
            return ThrottleResult(allowed=True)

        # 超過門檻（鎖定已過期或從未設定） → 仍拒絕直到視窗重置
        if record.failure_count >= max_failures:
            return ThrottleResult(
                allowed=False,
                reason=self._reason_for(attempt_key),
            )

        return ThrottleResult(allowed=True)

    def _escalation_is_live(self, record: LoginAttemptRecord, now: datetime) -> bool:
        """鎖定升級狀態是否仍在保留期內。"""
        return (
            record.lockout_count > 0
            and record.locked_until is not None
            and now - record.locked_until <= self._config.lockout_retention
        )

    def _reset_window(self, record: LoginAttemptRecord, now: datetime) -> None:
        """視窗過期時重置失敗計數。

        仍在保留期內的鎖定升級狀態（lockout_count）**必須**留下，否則
        「先 check 再 record」的呼叫端每輪都從基礎鎖定時間重新開始，
        §6.4 的指數退避沒有可達路徑。
        """
        if self._escalation_is_live(record, now):
            record.window_started_at = now
            record.failure_count = 0
            self._repo.upsert(record)
        else:
            self._repo.delete(record.attempt_key)

    def _increment(self, attempt_key: str, now: datetime) -> None:
        record = self._repo.get(attempt_key)

        if record is None:
            record = LoginAttemptRecord(
                attempt_key=attempt_key,
                window_started_at=now,
                failure_count=1,
            )
        elif record.locked_until is not None and now < record.locked_until:
            # 鎖定仍有效：**不得**重建記錄，否則 locked_until 與 lockout_count
            # 會被抹除，長於視窗的鎖定（例如 60 分鐘）會在 t0+16m 整個消失。
            record.failure_count += 1
        elif now - record.window_started_at > self._config.window:
            # 視窗過期且無有效鎖定 → 重新計數，但保留跨視窗的退避升級狀態；
            # 超過保留期才歸零，使退避狀態不依賴呼叫端有沒有先走 check 路徑。
            record.window_started_at = now
            record.failure_count = 1
            if not self._escalation_is_live(record, now):
                record.locked_until = None
                record.lockout_count = 0
        else:
            record.failure_count += 1

        self._repo.upsert(record)

    def _apply_lockout(
        self, attempt_key: str, max_failures: int, now: datetime
    ) -> None:
        """達門檻時開啟新一輪鎖定。

        鎖定期間再有失敗不會重新計算鎖定時間——「加倍」的單位是鎖定輪次，
        不是失敗次數，否則同一輪內連續失敗會讓時間灌到上限。
        """
        record = self._repo.get(attempt_key)
        if record is None or record.failure_count < max_failures:
            return
        if record.locked_until is not None and now < record.locked_until:
            return  # 同一輪鎖定尚未結束

        record.lockout_count += 1
        if attempt_key.startswith(ACCOUNT_KEY_PREFIX):
            lockout = self._calculate_lockout(record.lockout_count)
        else:
            # IP 維度不做指數退避（§6.4 僅要求「拒絕並記錄」）
            lockout = self._config.base_lockout
        record.locked_until = now + lockout
        self._repo.upsert(record)

    def _calculate_lockout(self, lockout_count: int) -> timedelta:
        """指數退避鎖定時間（每次再鎖定加倍，上限 60 分鐘）。

        第 1 輪 → base_lockout；第 n 輪 → base_lockout × 2^(n-1)，取上限。
        """
        exponent = max(0, lockout_count - 1)
        lockout_seconds = self._config.base_lockout.total_seconds() * (2 ** exponent)
        max_seconds = self._config.max_lockout.total_seconds()
        return timedelta(seconds=min(lockout_seconds, max_seconds))
